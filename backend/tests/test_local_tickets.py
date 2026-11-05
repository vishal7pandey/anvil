"""ANVIL-56: local ticket tracker — JSON files in status folders as an alternative to Jira."""

import json

import pytest

from anvil.api.app import _code_base
from anvil.tickets import LocalTracker
from anvil.tools.errors import ToolError
from anvil.tools.run_scope import run_tools


def make_ticket(directory, key, labels, **extra):
    data = {
        "key": key, "type": "Task", "status": "To Do", "summary": f"{key} summary",
        "description": f"Code base: branch `tc1/5776-base` of C:\\work\\rtems.\n\nDo the thing.",
        "labels": labels, "comments": [], "created": "2026-11-05T00:00:00+00:00", **extra,
    }
    path = directory / f"{key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_tracker_candidates_comment_transition_labels(tmp_path):
    tracker = LocalTracker(tmp_path)
    make_ticket(tmp_path / "todo", "RTEMS-1", ["anvil"])
    make_ticket(tmp_path / "todo", "RTEMS-2", [])  # no anvil label -> not a candidate
    assert [c["key"] for c in tracker.candidates()] == ["RTEMS-1"]

    tracker.comment("RTEMS-1", "run started")
    data = json.loads((tmp_path / "todo" / "RTEMS-1.json").read_text())
    assert data["comments"][-1]["body"] == "run started"

    assert tracker.transition("RTEMS-1", "In Progress") == {"key": "RTEMS-1", "status": "In Progress"}
    assert not (tmp_path / "todo" / "RTEMS-1.json").exists()
    assert json.loads((tmp_path / "in-progress" / "RTEMS-1.json").read_text())["status"] == "In Progress"
    with pytest.raises(ToolError, match="not a ticket status"):
        tracker.transition("RTEMS-1", "Sideways")

    tracker.set_labels("RTEMS-1", add=["anvil-done"], remove=["anvil"])
    assert json.loads((tmp_path / "in-progress" / "RTEMS-1.json").read_text())["labels"] == ["anvil-done"]


def test_tracker_create_and_search(tmp_path):
    tracker = LocalTracker(tmp_path)
    make_ticket(tmp_path / "done", "RTEMS-7", ["anvil"])
    out = tracker.create("RTEMS", "Story", "next thing", labels=["cra"])
    assert out["key"] == "RTEMS-8" and (tmp_path / "todo" / "RTEMS-8.json").exists()

    hits = tracker.search('labels = anvil AND statusCategory = "Done"')
    assert [h["key"] for h in hits] == ["RTEMS-7"]
    assert tracker.search('text ~ "summary"', 10)
    with pytest.raises(ToolError, match="cannot evaluate"):
        tracker.search("nonsense !!!")


def test_code_base_accepts_absolute_paths():
    assert _code_base("Code base: branch `tc1/5776-base` of C:\\work\\rtems.") == ("C:\\work\\rtems", "tc1/5776-base")
    assert _code_base("Code base: branch main of /home/u/rtems") == ("/home/u/rtems", "main")
    assert _code_base("Code base: branch `b` of github.com/o/r") == ("o/r", "b")


def test_run_tools_binds_jira_names_to_the_local_store(tmp_path, monkeypatch):
    monkeypatch.setenv("ANVIL_TICKETS_DIR", str(tmp_path))
    make_ticket(tmp_path / "todo", "RTEMS-1", ["anvil"])
    run = {"run_id": "r1", "trigger": "local:RTEMS-1", "jira_key": "RTEMS-1",
           "repo": "C:\\work\\rtems", "base_ref": "tc1/5776-base"}
    tools = {getattr(t, "__name__", str(t)): t for t in run_tools(run, object())}
    # Exact names: the skills call jira_* and the jira_create approval gate keys by name.
    assert list(tools) == ["jira_get", "jira_search", "jira_create",
                           "jira_comment", "jira_transition", "jira_set_labels",
                           # ANVIL-60: the local knowledge folder, same tool names as the REST set
                           "confluence_search", "confluence_get", "confluence_write",
                           "publish_evidence"]
    # bound by run, not by argument: a comment lands in the ticket file
    tools["jira_comment"]("evidence")
    data = json.loads((tmp_path / "todo" / "RTEMS-1.json").read_text())
    assert data["comments"][-1]["body"] == "evidence"


def test_run_tools_without_tickets_dir_fails_loud(monkeypatch):
    monkeypatch.delenv("ANVIL_TICKETS_DIR", raising=False)
    run = {"run_id": "r1", "trigger": "local:X-1", "jira_key": "X-1", "repo": "/x", "base_ref": "b"}
    with pytest.raises(ToolError, match="ANVIL_TICKETS_DIR"):
        run_tools(run, object())


def test_intake_starts_a_local_run_and_moves_the_ticket(tmp_path, monkeypatch):
    # No Atlassian env at all: the poll must come from the ticket store and never reach Jira.
    import asyncio

    from pydantic import SecretStr
    from sqlalchemy import select
    from test_api import SuccessfulExecutor

    from anvil.api.app import create_app, poll_jira_intake
    from anvil.api.config import Settings
    from anvil.api.models import Base, Run

    for var in ("ATLASSIAN_SITE", "ATLASSIAN_EMAIL", "ATLASSIAN_API_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    repo = tmp_path / "rtems"  # a real working copy: normalize_repository validates it exists
    (repo / ".git").mkdir(parents=True)
    make_ticket(tmp_path / "todo", "RTEMS-1", ["anvil"],
                description=f"Code base: branch `tc1/5776-base` of {repo}.")
    app = create_app(
        settings=Settings(demo_token=SecretStr("t"), demo_user_id="u", demo_user_email="u@x"),
        database_url=f"sqlite:///{(tmp_path / 'a.db').as_posix()}",
        executor=SuccessfulExecutor(), start_intake_loop=False, tickets_dir=str(tmp_path))
    Base.metadata.create_all(app.state.engine)

    started = asyncio.run(poll_jira_intake(app))
    assert len(started) == 1
    with app.state.session_factory() as db:
        run = db.scalars(select(Run).where(Run.trigger == "local:RTEMS-1")).one()
        assert run.repo == str(repo) and run.network == "offline"
    assert (tmp_path / "in-progress" / "RTEMS-1.json").exists()


def test_create_rejects_a_project_key_that_escapes_the_tickets_dir(tmp_path):
    """project="../x" must not write outside the tracker root (ANVIL-98)."""
    from anvil.tickets import LocalTracker
    from anvil.tools.errors import ToolError

    tracker = LocalTracker(tmp_path)
    with pytest.raises(ToolError, match="invalid project"):
        tracker.create("../x", "Task", "evil")
    assert not (tmp_path / "x-1.json").exists()
    assert not (tmp_path.parent / "x-1.json").exists()
    # a normal project still creates
    out = tracker.create("GATE", "Task", "ok")
    assert out["key"] == "GATE-1"


def test_jira_create_is_bound_to_the_runs_project(tmp_path, monkeypatch):
    """A run on RTEMS-1 can't create in another project (ANVIL-98)."""
    monkeypatch.setenv("ANVIL_TICKETS_DIR", str(tmp_path))
    make_ticket(tmp_path / "todo", "RTEMS-1", ["anvil"])
    run = {"run_id": "r1", "trigger": "local:RTEMS-1", "jira_key": "RTEMS-1",
           "repo": "C:\work\rtems", "base_ref": "tc1/5776-base"}
    tools = {t.__name__: t for t in run_tools(run, object())}
    with pytest.raises(ToolError, match="bound to this run's project"):
        tools["jira_create"]("OTHER", "Task", "evil ticket")
    assert tools["jira_create"]("RTEMS", "Task", "ok")["key"] == "RTEMS-2"


def test_publish_evidence_is_bound_to_the_runs_ticket(tmp_path, monkeypatch):
    """publish_evidence's key must be this run's ticket (ANVIL-98)."""
    monkeypatch.setenv("ANVIL_TICKETS_DIR", str(tmp_path))
    make_ticket(tmp_path / "todo", "RTEMS-1", ["anvil"])
    run = {"run_id": "r1", "trigger": "local:RTEMS-1", "jira_key": "RTEMS-1",
           "repo": "C:\work\rtems", "base_ref": "tc1/5776-base"}
    tools = {t.__name__: t for t in run_tools(run, object())}
    with pytest.raises(ToolError, match="bound to this run's ticket"):
        tools["publish_evidence"]({"key": "ANVIL-9", "summary": "wrong ticket"})
