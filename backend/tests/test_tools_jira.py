"""Response shapes follow the Jira Cloud REST v3 docs (hand-written, not recorded: no token yet)."""

import json

import httpx
import pytest
import respx

from anvil.tools import jira
from anvil.tools._atlassian import ToolError


@pytest.fixture
def atlassian_env(monkeypatch):
    for k, v in {"ATLASSIAN_SITE": "https://example.atlassian.net", "ATLASSIAN_EMAIL": "bot@example.com", "ATLASSIAN_API_TOKEN": "fake-token"}.items():
        monkeypatch.setenv(k, v)
    return "https://example.atlassian.net"


ISSUE = {
    "key": "RTEMS-1",
    "fields": {
        "summary": "Timer fires one tick late",
        "status": {"name": "To Do"},
        "issuetype": {"name": "Bug"},
        "labels": ["anvil"],
        "assignee": None,
        "description": {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Steps here"}]}]},
        "comment": {"comments": [{"author": {"displayName": "Vishal"}, "created": "2026-11-02", "body": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "ping"}]}]}}]},
    },
}
TRANSITIONS = {"transitions": [{"id": "21", "name": "Start", "to": {"name": "In Progress"}}, {"id": "31", "name": "Done", "to": {"name": "Done"}}]}


@respx.mock
def test_get_returns_plain_text_and_uses_basic_auth(atlassian_env):
    route = respx.get(f"{atlassian_env}/rest/api/3/issue/RTEMS-1").respond(json=ISSUE)
    out = jira.jira_get("RTEMS-1")
    assert out["description"] == "Steps here" and out["status"] == "To Do" and out["labels"] == ["anvil"]
    assert out["comments"] == [{"author": "Vishal", "created": "2026-11-02", "body": "ping"}]
    assert out["url"] == f"{atlassian_env}/browse/RTEMS-1"
    assert route.calls[0].request.headers["authorization"].startswith("Basic ")


@respx.mock
def test_search_posts_jql_to_new_endpoint(atlassian_env):
    route = respx.post(f"{atlassian_env}/rest/api/3/search/jql").respond(json={"issues": [ISSUE]})
    assert [i["key"] for i in jira.jira_intake_candidates()] == ["RTEMS-1"]
    sent = json.loads(route.calls[0].request.content)
    assert sent["jql"] == jira.INTAKE_JQL and "comment" not in sent["fields"]


@respx.mock
def test_comment_sends_adf(atlassian_env):
    route = respx.post(f"{atlassian_env}/rest/api/3/issue/RTEMS-1/comment").respond(201, json={"id": "100"})
    out = jira.jira_comment("RTEMS-1", "**fixed**")
    assert out["id"] == "100"
    body = json.loads(route.calls[0].request.content)["body"]
    assert body["type"] == "doc" and body["content"][0]["content"][0]["marks"] == [{"type": "strong"}]


@respx.mock
def test_transition_matches_target_status_name(atlassian_env):
    respx.get(f"{atlassian_env}/rest/api/3/issue/RTEMS-1/transitions").respond(json=TRANSITIONS)
    post = respx.post(f"{atlassian_env}/rest/api/3/issue/RTEMS-1/transitions").respond(204)
    assert jira.jira_transition("RTEMS-1", "in progress") == {"key": "RTEMS-1", "status": "In Progress"}
    assert json.loads(post.calls[0].request.content) == {"transition": {"id": "21"}}


@respx.mock
def test_transition_unknown_status_lists_options(atlassian_env):
    respx.get(f"{atlassian_env}/rest/api/3/issue/RTEMS-1/transitions").respond(json=TRANSITIONS)
    with pytest.raises(ToolError, match="Available: Done, In Progress"):
        jira.jira_transition("RTEMS-1", "In Review")


@respx.mock
def test_set_labels_and_create(atlassian_env):
    put = respx.put(f"{atlassian_env}/rest/api/3/issue/RTEMS-1").respond(204)
    jira.jira_set_labels("RTEMS-1", add=["anvil-in-review"], remove=["anvil"])
    assert json.loads(put.calls[0].request.content) == {"update": {"labels": [{"add": "anvil-in-review"}, {"remove": "anvil"}]}}
    with pytest.raises(ToolError):
        jira.jira_set_labels("RTEMS-1")

    post = respx.post(f"{atlassian_env}/rest/api/3/issue").respond(201, json={"key": "RTEMS-9"})
    assert jira.jira_create("RTEMS", "Task", "Upgrade lwIP", "Why", ["anvil"])["key"] == "RTEMS-9"
    fields = json.loads(post.calls[0].request.content)["fields"]
    assert fields["project"] == {"key": "RTEMS"} and fields["description"]["type"] == "doc"


@respx.mock
def test_http_error_becomes_tool_error_without_token(atlassian_env):
    respx.get(f"{atlassian_env}/rest/api/3/issue/NOPE-1").respond(404, json={"errorMessages": ["Issue does not exist"]})
    with pytest.raises(ToolError, match="HTTP 404: Issue does not exist") as e:
        jira.jira_get("NOPE-1")
    assert "fake-token" not in str(e.value)


def test_missing_credentials(monkeypatch):
    monkeypatch.setenv("ATLASSIAN_EMAIL", "")
    monkeypatch.setenv("ATLASSIAN_API_TOKEN", "")
    with respx.mock(assert_all_called=False) as mock:
        mock.route().mock(side_effect=httpx.ConnectError("must not be called"))
        with pytest.raises(ToolError, match="not set"):
            jira.jira_get("RTEMS-1")
