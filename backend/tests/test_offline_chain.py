"""ANVIL-87: the whole offline chain, no LLM — local ticket JSON -> intake -> local repo copy ->
scripted agent commits and calls jira_comment -> patch lands in private/runs/<run_id>/ and applies
with git am; the ticket JSON gains the comment and moves folders; the decoy branch never reaches
the sandbox; nothing touches Atlassian or GitHub. A second case ends in handoff.
"""

import asyncio
import json
import os
import subprocess
import uuid

import pytest
from langchain_core.messages import AIMessage
from pydantic import SecretStr
from sqlalchemy import select

docker = pytest.importorskip("docker")


def _image():
    try:
        client = docker.from_env()
        client.ping()
        client.images.get("anvil-sandbox-rtems:7.1")
        return "anvil-sandbox-rtems:7.1"
    except Exception:
        return None


IMG = _image()
pytestmark = pytest.mark.skipif(IMG is None, reason="Docker or the sandbox image is not available")


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def host_repo(tmp_path):
    """A working copy with 'main' plus a decoy 'fix' branch that must never reach the sandbox."""
    repo = tmp_path / "rtems"
    repo.mkdir()
    git("init", "-q", "-b", "main", cwd=repo)
    git("config", "user.email", "t@t", cwd=repo)
    git("config", "user.name", "t", cwd=repo)
    (repo / "init.c").write_text("int init(void) { return 0; }\n")
    git("add", "-A", cwd=repo)
    git("commit", "-qm", "base", cwd=repo)
    git("switch", "-qc", "fix", cwd=repo)
    (repo / "init.c").write_text("int init(void) { return 1; } /* the withheld fix */\n")
    git("commit", "-qam", "withheld fix", cwd=repo)
    git("switch", "-q", "main", cwd=repo)
    return repo


def _ticket(tickets_dir, key, repo):
    (tickets_dir / "todo").mkdir(parents=True, exist_ok=True)
    (tickets_dir / "todo" / f"{key}.json").write_text(json.dumps({
        "key": key, "type": "Task", "status": "To Do", "summary": f"{key}: fix the init code",
        "description": f"Code base: branch `main` of {repo}.\n\nMake init return 1.",
        "labels": ["anvil"], "comments": [], "created": "2026-11-05T00:00:00+00:00",
    }), encoding="utf-8")


def _scripted(action):
    def factory(config, sandbox, *, tools, on_handoff, checkpointer, store, skills):
        class Scripted:
            async def astream(self, *_args, **_kwargs):
                yield ((), "updates", {"model": {"messages": [AIMessage(content="starting")]}})
                action(sandbox, tools, on_handoff)
                yield ((), "updates", {"model": {"messages": [AIMessage(content="done")]}})

        return Scripted()

    return factory


@pytest.fixture
def offline_app(tmp_path, host_repo, monkeypatch):
    """The real API + real executor wiring; only the model is scripted and persistence stubbed."""
    from anvil.api.app import create_app
    from anvil.api.config import Settings
    from anvil.api.models import Base
    from anvil.api.runtime_factory import create_executor_from_environment

    for var in ("ATLASSIAN_SITE", "ATLASSIAN_EMAIL", "ATLASSIAN_API_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    # Nothing may reach Atlassian or GitHub: make the transports fail loudly if anything tries.
    import anvil.tools._atlassian as atlassian
    import anvil.tools.confluence as confluence
    import anvil.tools.github as github
    import anvil.tools.jira as jira

    for mod, name in ((atlassian, "request"), (jira, "request"), (confluence, "request"), (github, "_gh")):
        if hasattr(mod, name):
            monkeypatch.setattr(mod, name,
                                lambda *a, **k: pytest.fail(f"offline run must not call {mod.__name__}.{name}"))

    tickets = tmp_path / "tickets"
    runs_dir = tmp_path / "runs"
    monkeypatch.setenv("ANVIL_TICKETS_DIR", str(tickets))
    monkeypatch.setenv("ANVIL_RUNS_DIR", str(runs_dir))
    monkeypatch.setenv("ANVIL_EXECUTOR", "deepagent")

    executor = create_executor_from_environment()
    assert executor is not None
    executor.image = IMG

    async def _no_persistence():
        return None, None

    executor._persistence = _no_persistence  # the scripted agent needs no checkpointer

    app = create_app(
        settings=Settings(demo_token=SecretStr("t"), demo_user_id="u", demo_user_email="u@x"),
        database_url=f"sqlite:///{(tmp_path / 'a.db').as_posix()}",
        executor=executor, start_intake_loop=False, tickets_dir=str(tickets))
    Base.metadata.create_all(app.state.engine)
    return app, executor, tickets, runs_dir


def _run_once(app, executor, action):
    """Drive one ticket through poll -> scheduled run -> completion; returns the run row."""
    from anvil.api.app import poll_jira_intake
    from anvil.api.models import Run

    executor.agent_factory = _scripted(action)

    async def drive():
        # Same loop for the poll and the run tasks it schedules: asyncio.run would cancel them
        # on exit if the wait happened in a second loop.
        started = await poll_jira_intake(app)
        await asyncio.gather(*list(app.state.run_tasks.values()), return_exceptions=True)
        return started

    started = asyncio.run(drive())
    assert len(started) == 1
    with app.state.session_factory() as db:
        return db.scalars(select(Run).where(Run.id == started[0])).one()


def _cleanup_sandbox(session_id):
    """Remove only this test's container (other agents' anvil-sbx-* are not ours to touch)."""
    from anvil.runtime.executor import sandbox_name

    try:
        docker.from_env().containers.get(sandbox_name(session_id)).remove(force=True)
    except Exception:
        pass


def test_offline_chain_ticket_to_patch(host_repo, offline_app):
    app, executor, tickets, runs_dir = offline_app
    _ticket(tickets, "DEMO-1", host_repo)
    seen = {}

    def act(sandbox, tools, _on_handoff):
        by_name = {getattr(t, "__name__", ""): t for t in tools}
        seen["no_github"] = not any(n.startswith(("git_push", "github_")) for n in by_name)
        path = "/workspace/rtems"
        seen["refs"] = sandbox.execute(f"git -C {path} for-each-ref --format='%(refname)'").output
        res = sandbox.execute(f"cd {path} && echo 'return 1' > fix.txt && git add -A && "
                              f"git commit -qm 'DEMO-1: fix' && git rev-parse HEAD", timeout=60)
        seen["commit"] = res.exit_code == 0
        seen["comment"] = by_name["jira_comment"]("fix committed; ready for review")

    try:
        run = _run_once(app, executor, act)
    finally:
        _cleanup_sandbox(run.session_id)

    assert run.status == "succeeded"
    assert seen["no_github"] and seen["commit"]
    assert "fix" not in seen["refs"]  # the decoy branch never reached the sandbox
    assert seen["comment"]["url"].endswith(".json")

    patches = list(runs_dir.glob("*/*.patch"))
    assert patches, "no patch exported to private/runs"
    git("am", "-q", str(patches[0]), cwd=host_repo)
    assert (host_repo / "fix.txt").exists()

    data = json.loads((tickets / "in-progress" / "DEMO-1.json").read_text())
    assert any("fix committed" in c["body"] for c in data["comments"])


def test_offline_chain_handoff_writes_the_ticket(host_repo, offline_app):
    app, executor, tickets, _runs_dir = offline_app
    _ticket(tickets, "DEMO-2", host_repo)

    def act(_sandbox, _tools, on_handoff):
        on_handoff({"goal": "fix init", "tried": "approach A", "evidence": "still red",
                    "hypothesis": "needs hardware", "next_step": "a human reviews"})

    try:
        run = _run_once(app, executor, act)
    finally:
        _cleanup_sandbox(run.session_id)

    assert run.status == "handoff"
    data = json.loads((tickets / "in-progress" / "DEMO-2.json").read_text())
    assert any("handoff" in c["body"].casefold() or "needs hardware" in c["body"]
               for c in data["comments"])
    assert "anvil-needs-human" in data["labels"]
