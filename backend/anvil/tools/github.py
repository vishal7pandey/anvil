"""GitHub host tools over REST (httpx). Host process only; the token never reaches a sandbox (ADR-004)."""

import subprocess
import time
from typing import Any

import httpx
from pydantic_settings import BaseSettings, SettingsConfigDict

from anvil.tools._atlassian import REPO_ENV
from anvil.tools.errors import ToolError

_OK = {"success", "neutral", "skipped"}


_cli_token = ""


def _gh_cli_token() -> str:
    """The owner's `gh` login (same GitHub account as everything else on this laptop). Cached once found."""
    global _cli_token
    if not _cli_token:
        try:
            r = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=60)
            _cli_token = r.stdout.strip() if r.returncode == 0 else ""
        except (OSError, subprocess.TimeoutExpired):
            pass
    return _cli_token


class GitHubSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ENV, extra="ignore")
    github_token: str = ""
    github_owner: str = "vishal7pandey"
    github_api: str = "https://api.github.com"
    github_remote_base: str = "https://github.com"

    def model_post_init(self, _: Any) -> None:
        if not self.github_token:
            self.github_token = _gh_cli_token()


def full_name(repo: str) -> str:
    return repo if "/" in repo else f"{GitHubSettings().github_owner}/{repo}"


def _gh(method: str, path: str, **kwargs: Any) -> Any:
    s = GitHubSettings()
    if not s.github_token:
        raise ToolError("no GitHub credentials: set GITHUB_TOKEN or log in with `gh auth login`")
    headers = {"Authorization": f"Bearer {s.github_token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    with httpx.Client(base_url=s.github_api, headers=headers, timeout=30, follow_redirects=True) as c:
        r = c.request(method, path, **kwargs)
    if r.is_error:
        try:
            detail = r.json().get("message", r.text[:300])
        except ValueError:
            detail = r.text[:300]
        raise ToolError(f"{method} {path} -> HTTP {r.status_code}: {detail}")
    ctype = r.headers.get("content-type", "")
    return r.json() if "json" in ctype else r.text


def is_protected(path: str) -> bool:
    """Paths agents may never change: .github/ (workflows run with repo secrets; the gh token has workflow scope)."""
    return path.strip().strip('"').lower().startswith(".github/")


def _require_agent_branch(branch: str) -> None:
    if not branch.startswith("anvil/"):
        raise ToolError(f"refused: agent branches must start with 'anvil/', got '{branch}'")


def github_open_pr(repo: str, branch: str, title: str, body: str, base: str = "main", draft: bool = False) -> dict[str, Any]:
    """Open a pull request from an `anvil/...` branch (or return the open one). Title must start with the Jira key."""
    _require_agent_branch(branch)
    name = full_name(repo)
    owner = name.split("/")[0]
    existing = _gh("GET", f"/repos/{name}/pulls", params={"head": f"{owner}:{branch}", "state": "open"})
    pr = existing[0] if existing else _gh("POST", f"/repos/{name}/pulls", json={"title": title, "body": body, "head": branch, "base": base, "draft": draft})
    return {"number": pr["number"], "url": pr["html_url"], "head_sha": pr["head"]["sha"], "created": not existing}


def _log_tail(name: str, job_id: int, chars: int = 3000) -> str:
    try:
        return str(_gh("GET", f"/repos/{name}/actions/jobs/{job_id}/logs"))[-chars:]
    except ToolError as e:
        return f"(log unavailable: {e})"


def _check_state(name: str, pr: int, with_logs: bool) -> dict[str, Any]:
    sha = _gh("GET", f"/repos/{name}/pulls/{pr}")["head"]["sha"]
    runs = _gh("GET", f"/repos/{name}/commits/{sha}/check-runs", params={"per_page": 100})["check_runs"]
    statuses = _gh("GET", f"/repos/{name}/commits/{sha}/status")["statuses"]
    checks = [
        {"name": r["name"], "status": r["status"], "conclusion": r["conclusion"], "url": r["html_url"], "summary": (r.get("output") or {}).get("summary") or "", "_id": r["id"], "_app": (r.get("app") or {}).get("slug")}
        for r in runs
    ] + [
        {"name": s["context"], "status": "completed" if s["state"] != "pending" else "in_progress", "conclusion": {"pending": None}.get(s["state"], s["state"]), "url": s.get("target_url"), "summary": s.get("description") or ""}
        for s in statuses
    ]
    failing = [c for c in checks if c["status"] == "completed" and c["conclusion"] not in _OK]
    if not checks:
        state = "none"
    elif failing:
        state = "failure"
    elif any(c["status"] != "completed" for c in checks):
        state = "pending"
    else:
        state = "success"
    if with_logs:
        for c in failing:
            if c.get("_app") == "github-actions":
                c["log_tail"] = _log_tail(name, c["_id"])
    for c in checks:
        c.pop("_id", None)
        c.pop("_app", None)
    return {"pr": pr, "head_sha": sha, "state": state, "checks": checks, "failing": [c["name"] for c in failing]}


def github_checks(repo: str, pr: int, wait_seconds: int = 900, poll_seconds: int = 20) -> dict[str, Any]:
    """Wait (up to wait_seconds) for all CI checks on a PR's head commit to finish.

    Returns state success|failure|pending|none, every check with its conclusion and link, and for
    failed GitHub Actions jobs the tail of the job log so you can fix the cause.
    """
    name, deadline = full_name(repo), time.monotonic() + wait_seconds
    while True:
        result = _check_state(name, pr, with_logs=False)
        if result["state"] in {"success", "failure"} or time.monotonic() >= deadline:
            break
        time.sleep(poll_seconds)
    return _check_state(name, pr, with_logs=True) if result["state"] == "failure" else result


def github_pr_state(repo: str, pr: int, wait_seconds: int = 0, poll_seconds: int = 30) -> dict[str, Any]:
    """PR state for the merge watch: open|merged|closed plus review and issue comments.

    With wait_seconds > 0, polls until the PR is no longer open or the wait expires. On 'merged'
    the runtime transitions the Jira ticket to Done; there is no webhook (a laptop has no URL).
    """
    name = full_name(repo)
    deadline = time.monotonic() + wait_seconds
    while True:
        p = _gh("GET", f"/repos/{name}/pulls/{pr}")
        state = "merged" if p.get("merged_at") else p["state"]
        if state != "open" or time.monotonic() >= deadline:
            break
        time.sleep(poll_seconds)
    comments = [
        {"author": (c.get("user") or {}).get("login"), "created": c.get("created_at"), "body": (c.get("body") or "")[:2000]}
        for c in _gh("GET", f"/repos/{name}/issues/{pr}/comments", params={"per_page": 100})
    ]
    reviews = [
        {"author": (r.get("user") or {}).get("login"), "state": r.get("state"), "body": (r.get("body") or "")[:2000], "submitted": r.get("submitted_at")}
        for r in _gh("GET", f"/repos/{name}/pulls/{pr}/reviews", params={"per_page": 100})
    ]
    return {"pr": pr, "state": state, "merged_at": p.get("merged_at"), "merge_commit_sha": p.get("merge_commit_sha"),
            "head_sha": p["head"]["sha"], "draft": p.get("draft"), "title": p.get("title"), "url": p["html_url"],
            "comments": comments, "reviews": reviews}


def github_code_scanning_alerts(repo: str, pr: int) -> list[dict[str, Any]]:
    """Open code-scanning (CodeQL) alerts on a PR: rule, severity, message, file and line."""
    alerts = _gh("GET", f"/repos/{full_name(repo)}/code-scanning/alerts", params={"ref": f"refs/pull/{pr}/head", "state": "open", "per_page": 100})
    return [
        {
            "number": a["number"],
            "rule": a["rule"].get("id"),
            "severity": a["rule"].get("security_severity_level") or a["rule"].get("severity"),
            "message": (a.get("most_recent_instance") or {}).get("message", {}).get("text") or a["rule"].get("description"),
            "path": (a.get("most_recent_instance") or {}).get("location", {}).get("path"),
            "line": (a.get("most_recent_instance") or {}).get("location", {}).get("start_line"),
            "url": a["html_url"],
        }
        for a in alerts
    ]


def github_merge(repo: str, pr: int, method: str = "squash") -> dict[str, Any]:
    """Merge a PR from an `anvil/...` branch. Refuses unless every check on the head commit is green.

    The run's merge policy (human / agent-after-approval / agent) is enforced by the runtime before this is offered.
    """
    name = full_name(repo)
    head = _gh("GET", f"/repos/{name}/pulls/{pr}")["head"]["ref"]
    _require_agent_branch(head)
    files, page = [], 1
    while batch := _gh("GET", f"/repos/{name}/pulls/{pr}/files", params={"per_page": 100, "page": page}):
        files += [p for f in batch for p in (f["filename"], f.get("previous_filename") or "")]
        page += 1
    if blocked := sorted({p for p in files if is_protected(p)}):
        raise ToolError(f"refused: PR #{pr} changes protected paths ({', '.join(blocked[:10])}); agents may not change .github/")
    state = _check_state(name, pr, with_logs=False)
    if state["state"] != "success":
        raise ToolError(f"refused: checks on PR #{pr} are '{state['state']}' (failing: {', '.join(state['failing']) or 'none'})")
    r = _gh("PUT", f"/repos/{name}/pulls/{pr}/merge", json={"merge_method": method, "sha": state["head_sha"]})
    return {"pr": pr, "merged": r.get("merged", False), "sha": r.get("sha"), "message": r.get("message")}


GITHUB_TOOLS = [github_open_pr, github_checks, github_code_scanning_alerts, github_merge, github_pr_state]
