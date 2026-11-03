"""Response shapes follow the GitHub REST and SonarCloud Web API docs (hand-written, not recorded)."""

import json

import httpx
import pytest
import respx

from anvil.tools import github, sonar
from anvil.tools.errors import ToolError

API = "https://api.github.com/repos/vishal7pandey/rtems"
PR = {"number": 7, "html_url": "https://github.com/vishal7pandey/rtems/pull/7", "head": {"sha": "abc", "ref": "anvil/rtems-1-fix"}}


@pytest.fixture(autouse=True)
def gh_env(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "fake-gh-token")
    monkeypatch.setenv("GITHUB_OWNER", "vishal7pandey")
    monkeypatch.setenv("SONAR_HOST_URL", "https://sonarcloud.io")
    monkeypatch.setenv("SONAR_TOKEN", "")
    monkeypatch.setattr(github.time, "sleep", lambda s: None)


def run(name, status="completed", conclusion="success", id=1):
    return {"id": id, "name": name, "status": status, "conclusion": conclusion, "html_url": f"https://x/{name}", "output": {"summary": ""}, "app": {"slug": "github-actions"}}


def mock_files(*names):
    pages = {"1": [{"filename": n} for n in names]}
    respx.get(f"{API}/pulls/7/files").mock(side_effect=lambda req: httpx.Response(200, json=pages.get(req.url.params["page"], [])))


def mock_checks(runs, statuses=()):
    mock_files("cpukit/score/src/x.c")
    respx.get(f"{API}/pulls/7").respond(json=PR)
    respx.get(f"{API}/commits/abc/check-runs").respond(json={"check_runs": runs})
    respx.get(f"{API}/commits/abc/status").respond(json={"statuses": list(statuses)})


def test_falls_back_to_gh_cli_token(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "")
    monkeypatch.setattr(github, "_gh_cli_token", lambda: "from-gh-cli")
    assert github.GitHubSettings().github_token == "from-gh-cli"
    monkeypatch.setattr(github, "_gh_cli_token", lambda: "")
    with pytest.raises(ToolError, match="gh auth login"):
        github._gh("GET", "/user")


@respx.mock
def test_open_pr_creates_and_sends_bearer():
    respx.get(f"{API}/pulls").respond(json=[])
    respx.get(f"{API}/branches/anvil%2Frtems-1-fix").respond(json={"name": "anvil/rtems-1-fix"})
    post = respx.post(f"{API}/pulls").respond(201, json=PR)
    out = github.github_open_pr("rtems", "anvil/rtems-1-fix", "RTEMS-1: Fix timer", "body")
    assert out == {"number": 7, "url": PR["html_url"], "head_sha": "abc", "created": True}
    assert json.loads(post.calls[0].request.content)["head"] == "anvil/rtems-1-fix"
    assert post.calls[0].request.headers["authorization"] == "Bearer fake-gh-token"


@respx.mock
def test_open_pr_before_push_says_to_push_first():
    respx.get(f"{API}/pulls").respond(json=[])
    respx.get(f"{API}/branches/anvil%2Frtems-1-fix").respond(404, json={"message": "Branch not found"})
    post = respx.post(f"{API}/pulls").respond(422, json={"message": "Validation Failed"})
    with pytest.raises(ToolError, match="not on GitHub yet: call git_push first"):
        github.github_open_pr("rtems", "anvil/rtems-1-fix", "RTEMS-1: Fix timer", "body")
    assert not post.called


@respx.mock
def test_open_pr_returns_existing_and_refuses_non_agent_branch():
    respx.get(f"{API}/pulls").respond(json=[PR])
    assert github.github_open_pr("rtems", "anvil/rtems-1-fix", "t", "b")["created"] is False
    with pytest.raises(ToolError, match="must start with 'anvil/'"):
        github.github_open_pr("rtems", "main", "t", "b")


@respx.mock
def test_checks_failure_includes_log_tail():
    mock_checks([run("build"), run("tests", conclusion="failure", id=99)], [{"context": "SonarCloud", "state": "success", "target_url": "https://s"}])
    respx.get(f"{API}/actions/jobs/99/logs").respond(text="...\nFAIL sp01: expected 1 got 2", headers={"content-type": "text/plain"})
    out = github.github_checks("rtems", 7, wait_seconds=0)
    assert out["state"] == "failure" and out["failing"] == ["tests"]
    assert out["checks"][1]["log_tail"].endswith("expected 1 got 2")
    assert {c["name"] for c in out["checks"]} == {"build", "tests", "SonarCloud"}


@respx.mock
def test_checks_pending_then_times_out_and_none():
    mock_checks([run("build", status="in_progress", conclusion=None)])
    assert github.github_checks("rtems", 7, wait_seconds=0)["state"] == "pending"


@respx.mock
def test_merge_refuses_unless_green_then_merges_pinned_sha():
    mock_checks([run("build"), run("codeql", status="queued", conclusion=None)])
    with pytest.raises(ToolError, match="'pending'"):
        github.github_merge("rtems", 7)

    respx.reset()
    mock_checks([run("build")], [{"context": "SonarCloud", "state": "success"}])
    put = respx.put(f"{API}/pulls/7/merge").respond(json={"merged": True, "sha": "def", "message": "ok"})
    assert github.github_merge("rtems", 7)["merged"] is True
    assert json.loads(put.calls[0].request.content) == {"merge_method": "squash", "sha": "abc"}


@respx.mock
def test_merge_refuses_pr_touching_github_dir_even_when_green():
    mock_checks([run("build")])
    mock_files("cpukit/x.c", ".github/workflows/ci.yml")
    merge = respx.put(f"{API}/pulls/7/merge")
    with pytest.raises(ToolError, match=r"protected paths \(.github/workflows/ci.yml\)"):
        github.github_merge("rtems", 7)
    assert not merge.called


def test_is_protected():
    assert github.is_protected(".github/workflows/x.yml") and github.is_protected('".GitHub/dependabot.yml"')
    assert not github.is_protected("docs/.github-notes.md") and not github.is_protected("cpukit/.github/x")


@respx.mock
def test_merge_refuses_human_branch():
    respx.get(f"{API}/pulls/7").respond(json={**PR, "head": {"sha": "abc", "ref": "devin/anvil-13"}})
    with pytest.raises(ToolError, match="refused"):
        github.github_merge("rtems", 7)


@respx.mock
def test_pr_state_merged_includes_comments_and_reviews():
    respx.get(f"{API}/pulls/7").respond(json={**PR, "state": "closed", "merged_at": "2026-11-02T20:00:00Z", "merge_commit_sha": "m1", "draft": False, "title": "RTEMS-1: fix"})
    respx.get(f"{API}/issues/7/comments").respond(json=[{"user": {"login": "claude"}, "created_at": "t1", "body": "REVIEW (claude): APPROVE"}])
    respx.get(f"{API}/pulls/7/reviews").respond(json=[{"user": {"login": "codex"}, "state": "COMMENTED", "body": "nit", "submitted_at": "t2"}])
    out = github.github_pr_state("rtems", 7)
    assert out["state"] == "merged" and out["merge_commit_sha"] == "m1"
    assert out["comments"][0]["body"].startswith("REVIEW") and out["reviews"][0]["state"] == "COMMENTED"


@respx.mock
def test_pr_state_waits_until_not_open():
    calls = {"n": 0}

    def pr(req):
        calls["n"] += 1
        p = {**PR, "state": "open", "merged_at": None, "merge_commit_sha": None, "draft": True, "title": "t"}
        if calls["n"] >= 2:
            p["merged_at"] = "t"
        return httpx.Response(200, json=p)

    respx.get(f"{API}/pulls/7").mock(side_effect=pr)
    respx.get(f"{API}/issues/7/comments").respond(json=[])
    respx.get(f"{API}/pulls/7/reviews").respond(json=[])
    out = github.github_pr_state("rtems", 7, wait_seconds=60, poll_seconds=1)
    assert out["state"] == "merged" and calls["n"] == 2


@respx.mock
def test_code_scanning_alerts_for_pr_ref():
    route = respx.get(f"{API}/code-scanning/alerts").respond(json=[{
        "number": 3, "html_url": "https://a", "rule": {"id": "cpp/overflow", "severity": "warning", "security_severity_level": "high", "description": "d"},
        "most_recent_instance": {"message": {"text": "possible overflow"}, "location": {"path": "cpukit/x.c", "start_line": 10}},
    }])
    assert github.github_code_scanning_alerts("rtems", 7) == [
        {"number": 3, "rule": "cpp/overflow", "severity": "high", "message": "possible overflow", "path": "cpukit/x.c", "line": 10, "url": "https://a"}
    ]
    assert route.calls[0].request.url.params["ref"] == "refs/pull/7/head"


@respx.mock
def test_sonar_issues_anonymous():
    gate = respx.get("https://sonarcloud.io/api/qualitygates/project_status").respond(
        json={"projectStatus": {"status": "ERROR", "conditions": [{"metricKey": "new_bugs", "status": "ERROR"}, {"metricKey": "x", "status": "OK"}]}}
    )
    respx.get("https://sonarcloud.io/api/issues/search").respond(json={"total": 1, "issues": [
        {"key": "k1", "rule": "c:S1", "severity": "MAJOR", "type": "BUG", "message": "m", "component": "vishal7pandey_rtems:cpukit/x.c", "line": 5}
    ]})
    out = sonar.sonar_issues(7)
    assert out["quality_gate"] == "ERROR" and out["failed_conditions"] == ["new_bugs"]
    assert out["issues"][0]["path"] == "cpukit/x.c"
    assert "authorization" not in gate.calls[0].request.headers
    assert gate.calls[0].request.url.params["projectKey"] == "vishal7pandey_rtems"


@pytest.mark.parametrize("given", ["rtems", "vishal7pandey/rtems", "/workspace/rtems", "/workspace/rtems/", "rtems.git"])
def test_full_name_accepts_name_owner_name_and_sandbox_path(given):
    # M1 run c820a577 called git_push(repo="/workspace/rtems") and the host cloned github.com//workspace/rtems.
    assert github.full_name(given) == "vishal7pandey/rtems"


def test_full_name_refuses_empty():
    with pytest.raises(ToolError, match="empty repo"):
        github.full_name("  ")
