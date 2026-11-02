"""RunExecutor implementation: the bridge between the control API and the Deep Agents runtime.

One sandbox container per session (survives API restarts: it is found again by name), one
LangGraph thread per session (Postgres checkpointer), events in the docs/02 contract.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import re
import shlex
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import docker
from docker.errors import NotFound
from langgraph.types import Command

from anvil.runtime import prompts
from anvil.runtime.agent import RunConfig, build_agent
from anvil.runtime.events import EventMapper
from anvil.runtime.sandbox import DEFAULT_IMAGE, DockerSandbox, SandboxSpec

GITHUB_BASE = os.environ.get("ANVIL_GIT_BASE", "https://github.com")
RECURSION_LIMIT = int(os.environ.get("ANVIL_RECURSION_LIMIT", "400"))
SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"
SANDBOX_SKILLS = "/workspace/.anvil/skills/"


def skill_files(root: Path = SKILLS_DIR) -> list[tuple[str, bytes]]:
    """(sandbox path, content) for every file under backend/anvil/skills/."""
    return [(SANDBOX_SKILLS + p.relative_to(root).as_posix(), p.read_bytes())
            for p in sorted(root.rglob("*")) if p.is_file() and "__pycache__" not in p.parts]


def skills_hash(files: list[tuple[str, bytes]]) -> str:
    h = hashlib.sha256()
    for path, content in files:
        h.update(path.encode() + b"\0" + content)
    return h.hexdigest()[:16]

ToolsFactory = Callable[[DockerSandbox, dict[str, Any]], list[Any]]
AgentFactory = Callable[..., Any]


def sandbox_name(session_id: str) -> str:
    return f"anvil-sbx-{re.sub(r'[^a-zA-Z0-9_.-]', '', session_id)[:24]}"


# Run fields reach shell commands inside the sandbox; validate them at this boundary (they come
# from Jira tickets and API requests). Quoting alone isn't enough: a ref starting with "-" would be
# read by git as an option.
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_REF = re.compile(r"[A-Za-z0-9_.][A-Za-z0-9_./-]{0,199}")


def validate_repo(repo: str) -> str:
    if not isinstance(repo, str) or not _REPO.fullmatch(repo) or ".." in repo or repo.split("/")[1] in (".", ""):
        raise ValueError(f"invalid repo {repo!r}: expected 'owner/name'")
    return repo


def validate_ref(ref: str, what: str) -> str:
    if (not isinstance(ref, str) or not _REF.fullmatch(ref) or ".." in ref or "//" in ref
            or ref.endswith(("/", ".", ".lock"))):
        raise ValueError(f"invalid {what} {ref!r}")
    return ref


def repo_path(repo: str) -> str:
    return f"/workspace/{validate_repo(repo).split('/')[1]}"


def work_branch(run: dict[str, Any]) -> str:
    if run.get("branch"):
        return validate_ref(run["branch"], "branch")
    key = (run.get("jira_key") or f"run-{run['run_id'][:8]}").lower()
    return validate_ref(f"anvil/{key}", "branch")


def prompt_hash() -> str:
    text = "\n".join([prompts.MAIN, prompts.INVESTIGATOR, prompts.BUILDER, prompts.REVIEWER])
    return hashlib.sha256(text.encode()).hexdigest()[:16]


class DeepAgentExecutor:
    """Implements anvil.api.executor.RunExecutor (start / resume / cancel)."""

    def __init__(self, *, image: str | None = None, db_url: str | None = None,
                 tools_factory: ToolsFactory | None = None, agent_factory: AgentFactory = build_agent,
                 docker_client: docker.DockerClient | None = None) -> None:
        self.image = image or os.environ.get("ANVIL_SANDBOX_IMAGE", DEFAULT_IMAGE)
        self.db_url = db_url or os.environ.get("ANVIL_DATABASE_URL", "postgresql://anvil:anvil@localhost:5433/anvil")
        self.tools_factory = tools_factory or (lambda _sb, _run: [])
        self.agent_factory = agent_factory
        self._docker = docker_client
        self._cancel: dict[str, asyncio.Event] = {}
        self._stack: contextlib.AsyncExitStack | None = None
        self._checkpointer: Any = None
        self._store: Any = None

    # -- infrastructure --------------------------------------------------------------------

    @property
    def docker(self) -> docker.DockerClient:
        if self._docker is None:
            self._docker = docker.from_env()
        return self._docker

    async def _persistence(self) -> tuple[Any, Any]:
        if self._checkpointer is None:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
            from langgraph.store.postgres.aio import AsyncPostgresStore

            self._stack = contextlib.AsyncExitStack()
            self._checkpointer = await self._stack.enter_async_context(AsyncPostgresSaver.from_conn_string(self.db_url))
            self._store = await self._stack.enter_async_context(AsyncPostgresStore.from_conn_string(self.db_url))
            await self._checkpointer.setup()
            await self._store.setup()
        return self._checkpointer, self._store

    async def aclose(self) -> None:
        if self._stack is not None:
            await self._stack.aclose()
            self._stack = self._checkpointer = self._store = None

    def sandbox_for(self, run: dict[str, Any]) -> DockerSandbox:
        name = sandbox_name(run["session_id"])
        try:
            return DockerSandbox.attach(name, client=self.docker)
        except NotFound:
            return DockerSandbox.start(SandboxSpec(image=self.image, name=name, labels={
                "anvil.session_id": str(run["session_id"]), "anvil.repo": run["repo"]}), client=self.docker)

    def checkout(self, sandbox: DockerSandbox, run: dict[str, Any]) -> dict[str, Any]:
        """Clone only the base branch (answer-leak control: other refs may hold withheld fixes)."""
        repo = validate_repo(run["repo"])
        base = validate_ref(run.get("base_ref") or "main", "base_ref")
        branch = work_branch(run)
        path = repo_path(repo)
        qpath = shlex.quote(path)
        url = f"{GITHUB_BASE}/{repo}.git"
        script = (
            f"set -e; if [ ! -d {qpath}/.git ]; then "
            f"git clone --quiet --single-branch --branch {shlex.quote(base)} --depth 200 {shlex.quote(url)} {qpath}; fi; "
            f"cd {qpath}; git config user.name anvil-bot; git config user.email anvil-bot@users.noreply.github.com; "
            f"git remote set-url origin {shlex.quote(url)}; "
            f"git switch --quiet {shlex.quote(branch)} 2>/dev/null || git switch --quiet -c {shlex.quote(branch)}; "
            f"echo BASE_SHA=$(git rev-parse origin/{shlex.quote(base)}); echo HEAD_SHA=$(git rev-parse HEAD)"
        )
        sandbox.set_network("bridge")  # needed for the first clone; dropped again below for offline runs
        res = sandbox.execute(script, timeout=1800)
        if res.exit_code != 0:
            raise RuntimeError(f"checkout failed: {res.output[-1500:]}")
        found = dict(re.findall(r"^(BASE_SHA|HEAD_SHA)=(\w+)$", res.output, re.M))
        # Answer-leak control: --single-branch only limits the *default* fetch; with network the agent
        # could still `git fetch origin main` and read a withheld fix. Offline runs (the default) lose
        # the network after checkout. Builds and simulator tests don't need it; push runs on the host.
        files = skill_files()
        uploaded = sandbox.upload_files(files)
        failed = [r.path for r in uploaded if r.error]
        if failed:
            raise RuntimeError(f"could not upload skills: {failed}")
        network = run.get("network", "offline")
        sandbox.set_network("bridge" if network == "online" else None)
        return {"repo": repo, "path": path, "base_ref": base, "branch": branch, "network": network,
                "skills": [p for p, _ in files if p.endswith("SKILL.md")], "skills_hash": skills_hash(files),
                "base_sha": found.get("BASE_SHA"), "head_sha": found.get("HEAD_SHA")}

    def run_record(self, run: dict[str, Any], checkout: dict[str, Any]) -> dict[str, Any]:
        try:
            digest = self.docker.images.get(self.image).id
        except Exception:  # noqa: BLE001 - record what we can
            digest = None
        return {**checkout, "sandbox": sandbox_name(run["session_id"]), "sandbox_image": self.image,
                "sandbox_image_digest": digest, "model_aliases": {"main": "anvil-main", "fast": "anvil-fast"},
                "prompt_hash": prompt_hash(), "mode": run.get("mode"), "merge_policy": run.get("merge_policy"),
                "trigger": run.get("trigger", "ui")}

    # -- RunExecutor -----------------------------------------------------------------------

    @staticmethod
    def _mapper(run: dict[str, Any]) -> EventMapper:
        # The API owns seq (it re-numbers on persist); starting after its last seq keeps ours readable.
        mapper = EventMapper(run["run_id"])
        mapper.seq = int(run.get("last_event_seq") or 0)
        return mapper

    async def start(self, run: dict[str, Any], message: str) -> AsyncIterator[dict[str, Any]]:
        mapper = self._mapper(run)
        yield mapper.event("run.status", {"status": "running"})
        try:
            sandbox = await asyncio.to_thread(self.sandbox_for, run)
            checkout = await asyncio.to_thread(self.checkout, sandbox, run)
        except Exception as exc:  # noqa: BLE001 - surfaced to the user as a failed run
            yield mapper.event("message", {"role": "system", "text": f"Setup failed: {exc}"})
            yield mapper.event("run.status", {"status": "failed"})
            return
        yield mapper.event("run.record", self.run_record(run, checkout))
        task = (f"{message}\n\n[Anvil context] Repository {run['repo']} is checked out at {checkout['path']} "
                f"on branch {checkout['branch']} (base {checkout['base_ref']} @ {checkout['base_sha']}).")
        if run.get("jira_key"):
            task += f" Jira ticket: {run['jira_key']}."
        async for ev in self._stream(run, sandbox, mapper, {"messages": [{"role": "user", "content": task}]}):
            yield ev

    async def resume(self, run: dict[str, Any], decisions: list[dict[str, Any]]) -> AsyncIterator[dict[str, Any]]:
        mapper = self._mapper(run)
        sandbox = await asyncio.to_thread(self.sandbox_for, run)
        yield mapper.event("run.status", {"status": "running"})
        async for ev in self._stream(run, sandbox, mapper, Command(resume={"decisions": decisions})):
            yield ev

    async def cancel(self, run: dict[str, Any]) -> None:
        self._cancel.setdefault(run["run_id"], asyncio.Event()).set()

    # -- streaming -------------------------------------------------------------------------

    async def _stream(self, run: dict[str, Any], sandbox: DockerSandbox, mapper: EventMapper,
                      graph_input: Any) -> AsyncIterator[dict[str, Any]]:
        cancel = self._cancel.setdefault(run["run_id"], asyncio.Event())
        handoffs: list[dict[str, str]] = []
        checkpointer, store = await self._persistence()
        config = RunConfig(run_id=run["run_id"], repo=run["repo"], mode=run.get("mode", "interactive"),
                           merge_policy=run.get("merge_policy", "human"))
        agent = self.agent_factory(config, sandbox, tools=self.tools_factory(sandbox, run),
                                   on_handoff=handoffs.append, checkpointer=checkpointer, store=store,
                                   skills=[SANDBOX_SKILLS])
        waiting = False
        try:
            async for item in agent.astream(graph_input, config={"configurable": {"thread_id": str(run["session_id"])},
                                                                  "recursion_limit": RECURSION_LIMIT},
                                            stream_mode=["messages", "updates"], subgraphs=True):
                if cancel.is_set():
                    yield mapper.event("run.status", {"status": "cancelled"})
                    return
                for ev in mapper.map(item):
                    waiting = waiting or ev["type"] == "approval.request"
                    yield ev
        except Exception as exc:  # noqa: BLE001
            yield mapper.event("message", {"role": "system", "text": f"Run failed: {type(exc).__name__}: {exc}"})
            yield mapper.event("run.status", {"status": "failed"})
            return
        finally:
            self._cancel.pop(run["run_id"], None)
        if handoffs:
            yield mapper.event("artifact", {"kind": "handoff", "url": None, "title": "Handoff note",
                                            "note": handoffs[-1]})
            yield mapper.event("run.status", {"status": "handoff"})
        elif not waiting:
            yield mapper.event("run.status", {"status": "succeeded"})
