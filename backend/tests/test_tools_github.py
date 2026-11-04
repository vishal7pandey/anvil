"""Response shapes follow the GitHub REST and SonarCloud Web API docs (hand-written, not recorded)."""

import json

import httpx
import pytest
import respx

from anvil.tools import github, sonar
from anvil.tools.errors import ToolError

API = "https://api.github.com/repos/vishal7pandey/rtems"
PR = {"number": 7, "html_url": "https://github.com/vishal7pandey/rtems/pull/7", "head": {"sha": "abc", "ref": "anvil/rtems-1-fix"},
      "base": {"ref": "tc1/5776-base"}}


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


def mock_checks(runs, statuses=(), required=None):
    mock_files("cpukit/score/src/x.c")
    respx.get(f"{API}/pulls/7").respond(json=PR)
    protection = respx.get(f"{API}/branches/tc1%2F5776-base/protection/required_status_checks")
    if required is None:
        protection.respond(404, json={"message": "Branch not protected"})
    else:
        protection.respond(json={"contexts": required, "checks": [{"context": c} for c in required]})
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
    # A review has no PR: alerts on the branch (TC3, ANVIL-50).
    github.github_code_scanning_alerts("rtems", branch="tc2/upgrade-base-leon3")
    assert route.calls[1].request.url.params["ref"] == "refs/heads/tc2/upgrade-base-leon3"
    with pytest.raises(ToolError, match="exactly one"):
        github.github_code_scanning_alerts("rtems")


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


@respx.mock
def test_required_checks_gate_and_other_failures_are_advisory():
    # SonarCloud fails every agent PR on new-code coverage the pipeline never measures (ANVIL-32);
    # the base branch requires build-test and CodeQL, so the PR is green with Sonar advisory.
    mock_checks([run("build-test", id=1), run("CodeQL", id=2), run("SonarCloud", conclusion="failure", id=3)],
                required=["build-test", "CodeQL"])
    out = github.github_checks("rtems", 7, wait_seconds=0)
    assert out["state"] == "success" and out["advisory_failing"] == ["SonarCloud"] and out["failing"] == []


@respx.mock
def test_a_required_check_that_has_not_reported_is_pending():
    mock_checks([run("build-test", id=1)], required=["build-test", "CodeQL"])
    assert github.github_checks("rtems", 7, wait_seconds=0)["state"] == "pending"


@respx.mock
def test_case_required_checks_apply_when_the_branch_has_no_protection():
    # Owner decision 2026-11-04: SonarCloud is advisory; the case declares what gates (ANVIL-32).
    mock_checks([run("build-test", id=1), run("CodeQL (C/C++)", id=2), run("SonarCloud", conclusion="failure", id=3)])
    out = github.github_checks("rtems", 7, wait_seconds=0, default_required=["build-test", "CodeQL (C/C++)"])
    assert out["state"] == "success" and out["advisory_failing"] == ["SonarCloud"]
    respx.get(f"{API}/actions/jobs/3/logs").respond(text="quality gate failed")
    assert github.github_checks("rtems", 7, wait_seconds=0)["state"] == "failure"  # no case list: all gate


@respx.mock
def test_branch_protection_wins_over_the_case_list():
    mock_checks([run("build-test", id=1), run("lint", conclusion="failure", id=2)], required=["build-test", "lint"])
    respx.get(f"{API}/actions/jobs/2/logs").respond(text="lint failed")
    out = github.github_checks("rtems", 7, wait_seconds=0, default_required=["build-test"])
    assert out["state"] == "failure" and out["failing"] == ["lint"]


# -- ANVIL-76: waiting tools respect cancel and the run's time budget ---------------------------

@respx.mock
def test_checks_stop_on_cancel_within_one_slice():
    mock_checks([run("build", status="in_progress", conclusion=None)])
    calls = {"n": 0}

    def stop():
        calls["n"] += 1
        return "cancelled" if calls["n"] >= 2 else None

    out = github.github_checks("rtems", 7, wait_seconds=900, poll_seconds=20, should_stop=stop)
    assert out["state"] == "cancelled" and "cancelled" in out["note"]
    assert calls["n"] == 2  # stopped at the second one-second slice, not after 20 s or 900 s


@respx.mock
def test_checks_stop_at_budget_returns_last_state_with_note():
    mock_checks([run("build", status="in_progress", conclusion=None)])
    out = github.github_checks("rtems", 7, wait_seconds=900, should_stop=lambda: "budget")
    assert out["state"] == "pending" and "time budget ended" in out["note"]


def test_run_bound_wait_is_clamped_to_the_budget_and_sees_cancel(monkeypatch):
    import threading
    import time as _time
    from types import SimpleNamespace

    from anvil.tools.run_scope import run_tools

    seen = {}
    monkeypatch.setattr(github, "github_checks",
                        lambda repo, pr, wait_seconds, poll_seconds, default_required, should_stop:
                        seen.update(wait=wait_seconds, stop=should_stop) or {"state": "pending"})
    sandbox = SimpleNamespace(deadline=_time.monotonic() + 120, stop_requested=threading.Event())
    tools = {t.__name__: t for t in run_tools({"run_id": "r", "repo": "vishal7pandey/rtems", "base_ref": "main"}, sandbox)}
    tools["github_checks"](7)
    assert 110 <= seen["wait"] <= 120  # 900 asked, 2 minutes of budget left
    assert seen["stop"]() is None
    sandbox.stop_requested.set()
    assert seen["stop"]() == "cancelled"
    sandbox.stop_requested.clear()
    sandbox.deadline = _time.monotonic() - 1
    assert seen["stop"]() == "budget"
