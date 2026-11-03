"""Dry-run rendering of all four evidence templates from a recorded run record (ANVIL-27 acceptance)."""

import json
from pathlib import Path

import pytest
import respx

from anvil.tools import evidence
from anvil.tools.errors import ToolError
from test_tools_jira import atlassian_env  # noqa: F401


@pytest.fixture
def ev():
    return json.loads((Path(__file__).parent / "fixtures" / "evidence_rtems-1.json").read_text())


def test_pr_body(ev):
    body = evidence.pr_body(ev)
    for s in ("timercancel.c:97", "<= to <", "412/412", "success", "rtems/pull/12", "RTEMS-1", "run-3f9a2c", "Reproduced: psxtimer01"):
        assert s in body


def test_jira_comment(ev):
    c = evidence.jira_evidence_comment(ev)
    assert c.startswith("[anvil] Done in run run-3f9a2c")
    assert "$1.83" in c and "412000 tokens" in c and "2 human touches" in c
    assert "psxtimersuites" in c and "rtems/pull/12" in c


def test_confluence_page(ev):
    title, md = evidence.confluence_evidence(ev)
    assert title == "RTEMS-1 evidence: POSIX timer fires one tick late"
    assert "| PR | https://github.com/vishal7pandey/rtems/pull/12 |" in md
    assert "- Full psx subset on erc32-sis: 1284 passed, 0 failed." in md
    assert "one-tick-late" in md


def test_handoff_note(ev):
    note = {"goal": "fix the timer", "tried": "psxtimer01, build", "evidence": "tick 6", "hypothesis": "off by one", "next_step": "check _Timer_Cancel"}
    run = {"jira_key": "RTEMS-1", "run_id": "run-9", "branch": "anvil/rtems-1-wip"}
    out = evidence.handoff_note(run, note)
    assert "run-9" in out and "anvil/rtems-1-wip" in out and "off by one" in out and "RTEMS-1" in out


def test_publish_evidence_requires_evidence_space():
    with pytest.raises(ToolError, match="RTKB"):
        evidence.publish_evidence({"key": "X-1"}, space="ANVIL")


@respx.mock
def test_publish_evidence_writes_page_comment_labels(atlassian_env, ev):
    respx.get(f"{atlassian_env}/wiki/api/v2/spaces").respond(json={"results": [{"id": "7"}]})
    respx.get(f"{atlassian_env}/wiki/api/v2/pages").respond(json={"results": []})
    respx.post(f"{atlassian_env}/wiki/api/v2/pages").respond(json={"id": "9", "title": "t", "version": {"number": 1}, "_links": {"webui": "/p/9"}})
    respx.post(f"{atlassian_env}/rest/api/3/issue/RTEMS-1/comment").respond(201, json={"id": "50"})
    respx.put(f"{atlassian_env}/rest/api/3/issue/RTEMS-1").respond(204)
    out = evidence.publish_evidence(ev)
    assert out["confluence_page"]["url"].endswith("/p/9") and out["jira_comment"]["id"] == "50"
    sent = json.loads(respx.calls[-1].request.content)
    assert sent["update"]["labels"] == [{"add": "anvil-in-review"}]
