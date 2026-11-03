import json

import httpx

from anvil.kb.exclusions import GITLAB_ISSUE_SOURCE, GITLAB_MR_SOURCE, ExclusionGuard
from anvil.kb.gitlab import GitLabClient, fetch_gitlab_items, item_document

PROJECT = "rtems/rtos/rtems"


def _issue(iid, notes=0, updated="2026-01-01T00:00:00Z", **extra):
    return {"iid": iid, "title": f"Issue {iid}", "description": f"Body {iid}", "state": "closed",
            "labels": ["bug"], "created_at": "2025-12-01T00:00:00Z", "closed_at": "2026-01-01T00:00:00Z",
            "updated_at": updated, "user_notes_count": notes,
            "web_url": f"https://gitlab.example/{PROJECT}/-/issues/{iid}", **extra}


def _note(body, system=False, user="joel"):
    return {"body": body, "system": system, "author": {"username": user}, "created_at": "2026-01-02T00:00:00Z"}


def _gql_notes(request, by_iid):
    variables = json.loads(request.content)["variables"]
    nodes = [{"body": n["body"], "system": n["system"], "createdAt": n["created_at"], "author": n["author"]}
             for n in by_iid.get(variables["iid"], [])]
    return {"data": {"project": {"issue": {"notes": {
        "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": nodes}}}}}


class Recorder:
    """httpx transport that serves canned JSON by path and records every request."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        path = request.url.raw_path.decode().split("?")[0]  # keep %2F: url.path decodes it
        key = (path, request.url.params.get("page", "1"))
        handler = self.routes.get(key) or self.routes.get(path)
        if callable(handler):
            return handler(request)
        return httpx.Response(200, json=handler if handler is not None else [])


def _client(routes, **kw):
    rec = Recorder(routes)
    gl = GitLabClient("https://gitlab.example", client=httpx.Client(transport=httpx.MockTransport(rec)),
                      min_interval=0, sleep=lambda s: None, **kw)
    return gl, rec


ISSUES = "/api/v4/projects/rtems%2Frtos%2Frtems/issues"


def test_issues_paginate_oldest_first_and_fetch_notes_only_when_present():
    gl, rec = _client({
        (ISSUES, "1"): [_issue(1, notes=2, updated="2026-01-01T00:00:00Z"), _issue(2, updated="2026-01-02T00:00:00Z")],
        (ISSUES, "2"): [_issue(3, updated="2026-01-03T00:00:00Z")],
        "/api/graphql": lambda req: httpx.Response(200, json=_gql_notes(req, {
            "1": [_note("Root cause is in the scheduler."), _note("changed the label", system=True)]})),
    })
    cursors = []
    docs = list(fetch_gitlab_items(gl, PROJECT, "issues", per_page=2, on_page=cursors.append))

    assert [d.external_id for d in docs] == [f"{PROJECT}#1", f"{PROJECT}#2", f"{PROJECT}#3"]
    assert "Root cause is in the scheduler." in docs[0].text and "changed the label" not in docs[0].text
    assert docs[0].metadata["comments"] == 1 and docs[0].source == GITLAB_ISSUE_SOURCE
    note_calls = [c for c in rec.calls if c.url.path == "/api/graphql"]
    assert len(note_calls) == 1  # issue 2 and 3 have user_notes_count 0
    sent = json.loads(note_calls[0].content)
    assert sent["variables"] == {"path": PROJECT, "iid": "1", "after": None} and "issue(iid" in sent["query"]
    first = rec.calls[0].url.params
    assert first["state"] == "all" and first["sort"] == "asc" and first["order_by"] == "updated_at"
    assert cursors == ["2026-01-02T00:00:00Z", "2026-01-03T00:00:00Z"]


def test_resume_passes_saved_cursor_as_updated_after():
    gl, rec = _client({(ISSUES, "1"): []})
    assert list(fetch_gitlab_items(gl, PROJECT, "issues", updated_after="2026-01-03T00:00:00Z")) == []
    assert rec.calls[0].url.params["updated_after"] == "2026-01-03T00:00:00Z"


def test_rate_limit_waits_for_retry_after_then_succeeds():
    attempts = {"n": 0}

    def limited(request):
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "7"})
        return httpx.Response(200, json=[_issue(9)])

    slept = []
    rec = Recorder({ISSUES: limited})
    gl = GitLabClient("https://gitlab.example", client=httpx.Client(transport=httpx.MockTransport(rec)),
                      min_interval=0, sleep=slept.append)
    docs = list(fetch_gitlab_items(gl, PROJECT, "issues"))
    assert [d.external_id for d in docs] == [f"{PROJECT}#9"] and 7.0 in slept


def test_merge_request_document():
    mr = {"iid": 1493, "title": "posix: fix", "description": "Fixes it.", "state": "merged",
          "source_branch": "fix", "target_branch": "main", "labels": [], "created_at": "2026-01-01T00:00:00Z",
          "merged_at": "2026-01-05T00:00:00Z", "updated_at": "2026-01-05T00:00:00Z",
          "web_url": "https://gitlab.example/mr/1493"}
    doc = item_document(PROJECT, "merge_requests", mr, [_note("LGTM", user="sebhub")])
    assert doc.source == GITLAB_MR_SOURCE and doc.external_id == f"{PROJECT}!1493"
    assert "fix -> main" in doc.text and "merged 2026-01-05" in doc.text and "**sebhub**" in doc.text


def test_guard_understands_project_scoped_ids():
    guard = ExclusionGuard()  # TC1 defaults: issue 5776, MR 1493
    assert guard.is_excluded(GITLAB_ISSUE_SOURCE, f"{PROJECT}#5776")
    assert guard.is_excluded(GITLAB_ISSUE_SOURCE, "5776")  # legacy unscoped ids still work
    assert guard.is_excluded(GITLAB_MR_SOURCE, f"{PROJECT}!1493")
    assert not guard.is_excluded(GITLAB_ISSUE_SOURCE, f"{PROJECT}#6000")
    assert not guard.is_excluded(GITLAB_MR_SOURCE, f"{PROJECT}!6000")
