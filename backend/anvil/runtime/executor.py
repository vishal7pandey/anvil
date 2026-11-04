"""RunExecutor implementation: the bridge between the control API and the Deep Agents runtime.

One sandbox container per session (survives API restarts: it is found again by name), one
LangGraph thread per session (Postgres checkpointer), events in the docs/02 contract.
"""

from __future__ import annotations

from anvil.runtime.sandbox import host_run
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
from langgraph.errors import GraphRecursionError
from langgraph.types import Command

from anvil.runtime import prompts
from anvil.runtime.agent import RunConfig, build_agent
from anvil.runtime.events import EventMapper
from anvil.runtime import source
from anvil.runtime.sandbox import DEFAULT_IMAGE, DockerSandbox, SandboxSpec
from anvil.runtime.stuck import Budgets, StuckDetector

GITHUB_BASE = os.environ.get("ANVIL_GIT_BASE", "https://github.com")
# Submodule sources fetched from mirrors instead of hosts that can be down at checkout time:
# "orig=mirror;orig=mirror", applied as git url.<mirror>.insteadOf <orig> in the sandbox. Default:
# gitlab.rtems.org returned 500s and then timed out on rtems-lwip's rtems_waf submodule and failed
# the first TC3 run's setup (ANVIL-50); RTEMS's official GitHub mirrors carry the pinned commits.
GIT_URL_REWRITES = os.environ.get(
    "ANVIL_GIT_URL_REWRITES", "https://gitlab.rtems.org/rtems/tools/=https://github.com/RTEMS/")


def url_rewrites(spec: str = "") -> list[tuple[str, str]]:
    """(original prefix, mirror prefix) pairs from an "orig=mirror;..." spec; https URLs only."""
    pairs = []
    for item in (spec or GIT_URL_REWRITES).split(";"):
        if not item.strip():
            continue
        orig, _, mirror = item.strip().partition("=")
        if not (orig.startswith("https://") and mirror.startswith("https://")):
            raise ValueError(f"ANVIL_GIT_URL_REWRITES: '{item}' is not 'https://orig=https://mirror'")
        pairs.append((orig, mirror))
    return pairs
# Graph supersteps, not tool calls: one tool call costs ~3 (model, tools, middleware) and subagents
# add their own. A safety net only: it must sit well above the tool-call guard (Budgets.max_tool_calls).
RECURSION_LIMIT = int(os.environ.get("ANVIL_RECURSION_LIMIT", "2000"))
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
# push(sandbox, run, wip_branch) -> url | None ; report(run, note_markdown) -> None. Host-side, optional.
PushHook = Callable[[DockerSandbox, dict[str, Any], str], str | None]
# report(run, handoff) where handoff = {note, wip_branch, url, sandbox, markdown}. The host side
# formats its own Jira text (devin's evidence.handoff_note); markdown is a ready-made fallback.
ReportHook = Callable[[dict[str, Any], dict[str, Any]], None]
# verify(run, sandbox) -> problems. Host-side check of a finished run's result (ANVIL-32): an empty list
# means verified; anything else turns the run into a handoff that says what could not be verified.
VerifyHook = Callable[[dict[str, Any], Any], list[str]]
PROGRESS_TOOLS = {"write_file", "edit_file", "execute"}
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
    """/workspace/<name>: the GitHub repo name, or a local source's folder name (ANVIL-55)."""
    if source.is_local(repo):
        return f"/workspace/{source.local_name(source.validate_local(repo, check_exists=False))}"
    return f"/workspace/{validate_repo(repo).split('/')[1]}"


def graph_config(run: dict[str, Any]) -> dict[str, Any]:
    """The graph's checkpoint thread is the run, not the session (ANVIL-72).

    Keyed by session, a second run in the same session resumed the first run's checkpoint: its
    messages, todos and any pending approval interrupt. A run's own approval resume uses the same
    run id, so it still finds its interrupt. Context a follow-up run should have goes in its prompt
    or /memories/, never through a shared checkpoint.
    """
    return {"configurable": {"thread_id": str(run["run_id"])}, "recursion_limit": RECURSION_LIMIT}


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
                 docker_client: docker.DockerClient | None = None, budgets: Budgets | None = None,
                 push_hook: PushHook | None = None, report_hook: ReportHook | None = None,
                 verify_hook: VerifyHook | None = None, owner_id: str | None = None) -> None:
        self.budgets = budgets
        self.verify_hook = verify_hook
        self.push_hook = push_hook
        self.report_hook = report_hook
        self.image = image or os.environ.get("ANVIL_SANDBOX_IMAGE", DEFAULT_IMAGE)
        self.db_url = db_url or os.environ.get("ANVIL_DATABASE_URL", "postgresql://anvil:anvil@localhost:5433/anvil")
        self.owner_id = (
            owner_id
            or os.environ.get("ANVIL_INSTANCE_ID")
            or os.environ.get("ANVIL_OWNER_ID")
            or (self.db_url.rsplit("/", 1)[-1].split("?")[0] if "/" in self.db_url else "default")
        )
        self.tools_factory = tools_factory or (lambda _sb, _run: [])
        self.agent_factory = agent_factory
        self._docker = docker_client
        self._cancel: dict[str, asyncio.Event] = {}
        self._live: dict[str, Any] = {}  # run_id -> sandbox while the agent streams (ANVIL-76)
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
            return DockerSandbox.start(
                SandboxSpec(
                    image=self.image,
                    name=name,
                    labels={
                        "anvil.session_id": str(run["session_id"]),
                        "anvil.repo": run["repo"],
                        "anvil.owner": self.owner_id,
                    },
                ),
                client=self.docker,
            )

    def checkout(self, sandbox: DockerSandbox, run: dict[str, Any]) -> dict[str, Any]:
        """Clone only the base branch (answer-leak control: other refs may hold withheld fixes)."""
        if source.is_local(run["repo"]):
            return self.checkout_local(sandbox, run)
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
            # Submodules come in while the sandbox still has network: vendored-component upgrades
            # (TC2: lwip-upstream) need the upstream project's tags once the sandbox is offline.
            + "".join(f"git config --global url.{shlex.quote(m)}.insteadOf {shlex.quote(o)}; "
                      for o, m in url_rewrites())
            + f"if [ -f .gitmodules ]; then git submodule update --init --quiet; fi; "
            f"git switch --quiet {shlex.quote(branch)} 2>/dev/null || git switch --quiet -c {shlex.quote(branch)}; "
            f"echo BASE_SHA=$(git rev-parse origin/{shlex.quote(base)}); echo HEAD_SHA=$(git rev-parse HEAD)"
        )
        sandbox.set_network("bridge")  # needed for the first clone; dropped again below for offline runs
        res = host_run(sandbox, script, timeout=1800)
        if res.exit_code != 0:
            # Say what failed even when the command printed nothing (rehearsal 3e7db2f9: "checkout failed: ").
            raise RuntimeError(f"checkout failed (exit {res.exit_code}): "
                               f"{res.output[-1500:].strip() or 'no output'}")
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

    def checkout_local(self, sandbox: DockerSandbox, run: dict[str, Any]) -> dict[str, Any]:
        """A local source (ANVIL-55): the host copies a shallow single-branch .git of the base branch in.

        The sandbox never gets network for a local source, not even for the checkout: nothing in it
        has to be fetched, and nothing in it may be pushed.
        """
        repo = source.validate_local(run["repo"])
        # No branch given: a working copy's current branch, or 'main' for a folder/archive snapshot (ANVIL-91).
        base = validate_ref(run.get("base_ref") or source.default_base(repo), "base_ref")
        run["base_ref"] = base  # the export diffs against the same base
        kind, uncommitted = source.local_kind(repo), source.uncommitted_changes(repo)
        branch = work_branch(run)
        path = repo_path(repo)
        sandbox.set_network(None)
        sandbox.upload_tar("/workspace", source.local_copy_tar(repo, base))
        res = host_run(sandbox, source.local_checkout_script(path, base, branch), timeout=900)
        if res.exit_code != 0:
            raise RuntimeError(f"local checkout failed (exit {res.exit_code}): "
                               f"{res.output[-1500:].strip() or 'no output'}")
        found = dict(re.findall(r"^(BASE_SHA|HEAD_SHA)=(\w+)$", res.output, re.M))
        files = skill_files()
        failed = [r.path for r in sandbox.upload_files(files) if r.error]
        if failed:
            raise RuntimeError(f"could not upload skills: {failed}")
        return {"repo": repo, "path": path, "base_ref": base, "branch": branch, "network": "offline",
                "source": "local", "source_kind": kind, "uncommitted_changes": uncommitted,
                "skills": [p for p, _ in files if p.endswith("SKILL.md")],
                "skills_hash": skills_hash(files), "base_sha": found.get("BASE_SHA"), "head_sha": found.get("HEAD_SHA")}

    def export_local(self, sandbox: DockerSandbox, run: dict[str, Any], path: str, branch: str) -> dict[str, Any]:
        return source.export_result(sandbox, run, path, branch)

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
        from anvil.logs import record_commands, set_log_context

        set_log_context(run_id=run.get("run_id"), session_id=run.get("session_id"),
                        agent=run.get("trigger", "").split(":")[0])
        mapper = self._mapper(run)
        yield mapper.event("run.status", {"status": "running"})
        try:
            sandbox = await asyncio.to_thread(self.sandbox_for, run)
            record_commands(sandbox, run["run_id"])  # full output to private/runs/<id>/commands/ (ANVIL-84)
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
        if checkout.get("source_kind") in ("folder", "archive"):
            task += (" The code base is a snapshot of a project folder without its own git history: "
                     "commit your work on this branch as usual; it is exported as a patch.")
        if checkout.get("uncommitted_changes"):
            task += (f" Note: the original working copy has {checkout['uncommitted_changes']} uncommitted "
                     f"change(s) that are not part of this checkout.")
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
        # A tool may be sleeping in a host thread (CI polling): tell it too, not only the stream loop.
        live = self._live.get(run["run_id"])
        if live is not None and hasattr(live, "stop_requested"):
            live.stop_requested.set()

    # -- streaming -------------------------------------------------------------------------

    async def _stream(self, run: dict[str, Any], sandbox: DockerSandbox, mapper: EventMapper,
                      graph_input: Any) -> AsyncIterator[dict[str, Any]]:
        cancel = self._cancel.setdefault(run["run_id"], asyncio.Event())
        self._live[run["run_id"]] = sandbox
        handoffs: list[dict[str, str]] = []
        # Commands run in the run's repo checkout, for the agent and its subagents alike.
        if hasattr(sandbox, "set_workdir"):
            sandbox.set_workdir(repo_path(run["repo"]))
        checkpointer, store = await self._persistence()
        config = RunConfig(run_id=run["run_id"], repo=run["repo"], mode=run.get("mode", "interactive"),
                           merge_policy=run.get("merge_policy", "human"))
        agent = self.agent_factory(config, sandbox, tools=self.tools_factory(sandbox, run),
                                   on_handoff=handoffs.append, checkpointer=checkpointer, store=store,
                                   skills=[SANDBOX_SKILLS])
        waiting = False
        detector = StuckDetector(self.budgets)
        # The detector checks the time budget between events; the deadline makes the sandbox enforce
        # it inside a long command too (ANVIL-41). Cleared in `finally` so the handoff can still run.
        sandbox.deadline = detector.deadline
        path = repo_path(run["repo"])
        recent: list[dict[str, Any]] = []
        stuck: str | None = None
        try:
            async for item in agent.astream(graph_input, config=graph_config(run),
                                            stream_mode=["messages", "updates"], subgraphs=True):
                if cancel.is_set():
                    yield mapper.event("run.status", {"status": "cancelled"})
                    return
                for ev in mapper.map(item):
                    waiting = waiting or ev["type"] == "approval.request"
                    if ev["type"] == "sandbox.exec" and hasattr(sandbox, "take_timing"):
                        # Exact command time from the sandbox (ANVIL-83), not the tools step's.
                        ev["data"].update(sandbox.take_timing(ev["data"].get("cmd", "")) or {})
                    if ev["type"] in ("tool.call", "sandbox.exec"):
                        recent = (recent + [ev])[-12:]
                    if ev["type"] == "tool.result" and ev["data"].get("name") in PROGRESS_TOOLS:
                        detector.note_diff_state(await asyncio.to_thread(self.diff_state, sandbox, path))
                    stuck = stuck or detector.observe(ev)
                    yield ev
                # ANVIL-77: a handoff call ends the run like stuck does. Its note is already in
                # `handoffs`; keep streaming only until this item's events are out, then _handoff.
                if stuck or handoffs:
                    break
        except GraphRecursionError:
            # Out of graph steps is a budget, not a crash: hand off with the WIP like any other budget.
            stuck = f"graph step limit reached ({RECURSION_LIMIT} steps)"
        except Exception as exc:  # noqa: BLE001
            yield mapper.event("message", {"role": "system", "text": f"Run failed: {type(exc).__name__}: {exc}"})
            yield mapper.event("run.status", {"status": "failed"})
            return
        finally:
            self._cancel.pop(run["run_id"], None)
            self._live.pop(run["run_id"], None)
            sandbox.deadline = None
        if stuck or handoffs:
            note = handoffs[-1] if handoffs else self.auto_note(run, stuck or "", recent)
            async for ev in self._handoff(run, sandbox, mapper, path, note, stuck):
                yield ev
        elif not waiting:
            problems = await self._verify(run, sandbox)
            if not problems:
                if source.is_local(run["repo"]):
                    async for ev in self._export_local(run, sandbox, mapper, path, work_branch(run)):
                        yield ev
                yield mapper.event("run.status", {"status": "succeeded"})
                return
            # The agent finished, but its result does not check out: a human takes over, told exactly why.
            # Its own branch and PR are already pushed, so no WIP branch is made.
            note = {"goal": run.get("jira_key") or "see the run's first message",
                    "tried": "The agent finished and reported its work as done.",
                    "evidence": "\n".join(f"- {p}" for p in problems),
                    "hypothesis": "The result could not be verified, so the run is not counted as succeeded.",
                    "next_step": "Check the PR and its CI; fix what is listed above, or re-run with guidance."}
            async for ev in self._handoff(run, sandbox, mapper, path, note,
                                          "the result could not be verified: " + "; ".join(problems),
                                          push_wip=False):
                yield ev

    async def _export_local(self, run: dict[str, Any], sandbox: DockerSandbox, mapper: EventMapper, path: str,
                            branch: str) -> AsyncIterator[dict[str, Any]]:
        """Write a local source's result to the host and say where (an artifact the UI and ticket can link)."""
        try:
            out = await asyncio.to_thread(self.export_local, sandbox, run, path, branch)
        except Exception as exc:  # noqa: BLE001 - the run's outcome stands; say the export failed
            yield mapper.event("message", {"role": "system", "text": f"Exporting the result failed: {exc}"})
            return
        if not out.get("patch"):
            yield mapper.event("message", {"role": "system", "text": f"No commits on `{branch}`: nothing to export."})
            return
        yield mapper.event("artifact", {"kind": "patch", "url": out["patch"],
                                        "title": f"{out['commits']} commit(s) on {branch} as a patch",
                                        "note": {"patch": out["patch"], "bundle": out["bundle"],
                                                 "apply": "git am < the .patch file, or git fetch the .bundle"}})

    async def _verify(self, run: dict[str, Any], sandbox: DockerSandbox) -> list[str]:
        if not self.verify_hook:
            return []
        try:
            return list(await asyncio.to_thread(self.verify_hook, run, sandbox))
        except Exception as exc:  # noqa: BLE001 - an unverifiable result is not a success
            return [f"the outcome check itself failed ({type(exc).__name__}: {exc})"]

    # -- handoff ---------------------------------------------------------------------------

    @staticmethod
    def diff_state(sandbox: DockerSandbox, path: str) -> str:
        # Tracked edits, plus the content of untracked files that aren't ignored (a rewritten config.ini
        # is progress; build/ output is gitignored and is not).
        res = host_run(sandbox, 
            f"cd {shlex.quote(path)} 2>/dev/null && (git diff HEAD; git status --porcelain; "
            f"git ls-files -o --exclude-standard -z | xargs -0 -r sha1sum) | sha1sum", timeout=60)
        return res.output.strip()[:40]

    @staticmethod
    def auto_note(run: dict[str, Any], reason: str, recent: list[dict[str, Any]]) -> dict[str, str]:
        lines = []
        for e in recent[-8:]:
            what = e["data"].get("cmd") or e["data"].get("name")
            exit_part = f" -> exit {e['data'].get('exit_code')}" if e["type"] == "sandbox.exec" else ""
            lines.append(f"- `{what}`{exit_part}")
        last_fail = next((e for e in reversed(recent) if e["type"] == "sandbox.exec"
                          and e["data"].get("exit_code") not in (0, None)), None)
        if last_fail:
            tail = last_fail["data"]["output_tail"][-1200:]
            evidence = f"`{last_fail['data']['cmd']}`:\n```\n{tail}\n```"
        else:
            evidence = "(no failing command recorded)"
        return {"goal": run.get("jira_key") or "see the run's first message",
                "tried": "\n".join(lines) or "(none)",
                "evidence": evidence, "hypothesis": f"The runtime stopped the agent: {reason}.",
                "next_step": "Read the evidence, attach to the kept sandbox if needed, and decide whether to "
                             "re-run with more guidance or take over on the WIP branch."}

    async def _handoff(self, run: dict[str, Any], sandbox: DockerSandbox, mapper: EventMapper, path: str,
                       note: dict[str, str], reason: str | None, *, push_wip: bool = True) -> AsyncIterator[dict[str, Any]]:
        if reason:
            yield mapper.event("message", {"role": "system", "text": f"Stopping and handing off: {reason}."})
        wip = f"{work_branch(run)}-wip" if push_wip else work_branch(run)
        url = None
        if push_wip:
            key = run.get("jira_key") or "ANVIL"
            message = f"{key}: WIP handoff (Anvil run {run['run_id']})"
            commit = await asyncio.to_thread(host_run, sandbox, (
                f"cd {shlex.quote(path)} && git add -A && git commit -q --no-verify --allow-empty -m {shlex.quote(message)} "
                f"&& git branch -f {shlex.quote(wip)} HEAD && git rev-parse HEAD"), timeout=120)
            if commit.exit_code == 0 and source.is_local(run["repo"]):
                # No GitHub for a local source: the WIP goes to private/runs/<run_id>/ (ANVIL-55).
                async for ev in self._export_local(run, sandbox, mapper, path, wip):
                    if ev["type"] == "artifact":
                        url = ev["data"].get("url")
                    yield ev
            elif commit.exit_code == 0 and self.push_hook:
                try:
                    url = await asyncio.to_thread(self.push_hook, sandbox, run, wip)
                except Exception as exc:  # noqa: BLE001 - handoff must still complete
                    yield mapper.event("message", {"role": "system", "text": f"WIP push failed: {exc}"})
            branch_line = f"WIP branch: `{wip}`" + (f" ({url})" if url else " (not pushed)")
        elif source.is_local(run["repo"]):
            # Unverified local result: still hand the person the agent's commits (ANVIL-55).
            async for ev in self._export_local(run, sandbox, mapper, path, wip):
                if ev["type"] == "artifact":
                    url = ev["data"].get("url")
                yield ev
            branch_line = f"The agent's branch `{wip}`" + (f" exported to {url}" if url else " (nothing exported)")
        else:
            branch_line = "The agent's own branch and PR are as it pushed them."
        markdown = "\n\n".join([
            "**Anvil handed this task back to a human.**",
            f"**Goal:** {note.get('goal')}",
            f"**Tried:**\n{note.get('tried')}",
            f"**Evidence:**\n{note.get('evidence')}",
            f"**Hypothesis:** {note.get('hypothesis')}",
            f"**Next step:** {note.get('next_step')}",
            f"{branch_line}\nSandbox kept: `{sandbox.id}`",
        ])
        if self.report_hook:
            try:
                handoff = {"note": note, "wip_branch": wip, "url": url, "sandbox": sandbox.id, "markdown": markdown}
                await asyncio.to_thread(self.report_hook, run, handoff)
            except Exception as exc:  # noqa: BLE001
                yield mapper.event("message", {"role": "system", "text": f"Handoff report failed: {exc}"})
        yield mapper.event("artifact", {"kind": "handoff", "url": url, "title": f"Handoff note ({wip})",
                                        "note": note, "markdown": markdown})
        yield mapper.event("run.status", {"status": "handoff"})
