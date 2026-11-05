"""GitHub host tools over REST (httpx). Host process only; the token never reaches a sandbox (ADR-004)."""

import subprocess
import time
from typing import Callable, Any
from urllib.parse import quote

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
    """'owner/name' for a repo given as 'owner/name', 'name' or the sandbox path '/workspace/name'.

    Models pass the checkout path they have been working in (M1 run c820a577 called
    git_push(repo="/workspace/rtems")); that names the same repo, so accept it.
    """
    repo = repo.strip().rstrip("/").removesuffix(".git")
    if repo.startswith("/"):
        repo = repo.rsplit("/", 1)[-1]
    if not repo:
        raise ToolError("refused: empty repo name")
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


def _require_pushed(name: str, branch: str) -> None:
    """GitHub answers a PR for an unpushed branch with a bare 422 "Validation Failed", which the
    agent could not act on (run 7e81c6a2, ANVIL-40). Say what to do instead."""
    try:
        _gh("GET", f"/repos/{name}/branches/{quote(branch, safe='')}")
    except ToolError as exc:
        if "HTTP 404" in str(exc):
            raise ToolError(f"branch '{branch}' is not on GitHub yet: call git_push first, "
                            "then github_open_pr") from None
        raise


def github_open_pr(repo: str, branch: str, title: str, body: str, base: str = "main", draft: bool = False) -> dict[str, Any]:
    """Open a pull request from an `anvil/...` branch (or return the open one). Title must start with the Jira key."""
    _require_agent_branch(branch)
    name = full_name(repo)
    owner = name.split("/")[0]
    existing = _gh("GET", f"/repos/{name}/pulls", params={"head": f"{owner}:{branch}", "state": "open"})
    if not existing:
        _require_pushed(name, branch)
    pr = existing[0] if existing else _gh("POST", f"/repos/{name}/pulls", json={"title": title, "body": body, "head": branch, "base": base, "draft": draft})
    return {"number": pr["number"], "url": pr["html_url"], "head_sha": pr["head"]["sha"], "created": not existing}


def _log_tail(name: str, job_id: int, chars: int = 3000) -> str:
    try:
        return str(_gh("GET", f"/repos/{name}/actions/jobs/{job_id}/logs"))[-chars:]
    except ToolError as e:
        return f"(log unavailable: {e})"


def required_checks(name: str, branch: str) -> list[str] | None:
    """The status checks the branch's protection requires, or None when it requires none."""
    try:
        rsc = _gh("GET", f"/repos/{name}/branches/{quote(branch, safe='')}/protection/required_status_checks")
    except ToolError as exc:
        if "HTTP 404" in str(exc):
            return None
        raise
    names = list(rsc.get("contexts") or []) + [c["context"] for c in rsc.get("checks") or []]
    return list(dict.fromkeys(names)) or None


def _check_state(name: str, pr: int, with_logs: bool, default_required: list[str] | None = None) -> dict[str, Any]:
    pull = _gh("GET", f"/repos/{name}/pulls/{pr}")
    sha = pull["head"]["sha"]
    # Which checks gate a merge is the repository's decision (branch protection on the PR's base), not
    # ours: SonarCloud's gate fails every agent PR on "coverage on new code < 80%", a metric this
    # pipeline never measures (TC1 rehearsals, ANVIL-32). Without protection, every check gates.
    # Branch protection wins; otherwise the caller's default (a case's [ci] required_checks).
    required = required_checks(name, pull["base"]["ref"]) or default_required
    runs = _gh("GET", f"/repos/{name}/commits/{sha}/check-runs", params={"per_page": 100})["check_runs"]
    statuses = _gh("GET", f"/repos/{name}/commits/{sha}/status")["statuses"]
    checks = [
        {"name": r["name"], "status": r["status"], "conclusion": r["conclusion"], "url": r["html_url"], "summary": (r.get("output") or {}).get("summary") or "", "_id": r["id"], "_app": (r.get("app") or {}).get("slug")}
        for r in runs
    ] + [
        {"name": s["context"], "status": "completed" if s["state"] != "pending" else "in_progress", "conclusion": {"pending": None}.get(s["state"], s["state"]), "url": s.get("target_url"), "summary": s.get("description") or ""}
        for s in statuses
    ]
    gating = [c for c in checks if required is None or c["name"] in required]
    failed = [c for c in checks if c["status"] == "completed" and c["conclusion"] not in _OK]
    failing = [c for c in failed if c in gating]
    missing = [r for r in required or [] if r not in {c["name"] for c in checks}]
    if not checks:
        state = "none"
    elif failing:
        state = "failure"
    elif missing or any(c["status"] != "completed" for c in gating):
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
    return {"pr": pr, "head_sha": sha, "state": state, "checks": checks, "failing": [c["name"] for c in failing],
            "required": required, "advisory_failing": [c["name"] for c in failed if c not in gating]}


StopCheck = Callable[[], "str | None"]


def _pause(seconds: float, should_stop: StopCheck | None) -> str | None:
    """Sleep up to `seconds` in short slices; return why we stopped early ("cancelled", "budget") or None.

    Polling tools used to sleep for up to 15 min regardless: a cancel waited for the sleep and a run
    with 2 minutes of budget left could wait 15 (DFMEA F08, ANVIL-76).
    """
    left = max(0.0, seconds)
    while True:  # counted slices, not the clock: a stubbed time.sleep in tests must not busy-wait
        if should_stop and (reason := should_stop()):
            return reason
        if left <= 0:
            return None
        step = min(1.0, left)
        time.sleep(step)
        left -= step


def _stopped_note(reason: str) -> str:
    return ("The run was cancelled while waiting." if reason == "cancelled" else
            "The run's time budget ended while waiting: this is the last state seen, not a final one.")


def github_checks(repo: str, pr: int, wait_seconds: int = 900, poll_seconds: int = 20,
                  default_required: list[str] | None = None, should_stop: StopCheck | None = None) -> dict[str, Any]:
    """Wait (up to wait_seconds) for the CI checks that gate a PR to finish.

    The gating checks are those the base branch's protection requires (all checks if it requires
    none). Returns state success|failure|pending|none, every check with its conclusion and link,
    `advisory_failing` for failed checks that don't gate, and for failed GitHub Actions jobs the tail
    of the job log so you can fix the cause.
    """
    name, deadline = full_name(repo), time.monotonic() + wait_seconds
    while True:
        result = _check_state(name, pr, with_logs=False, default_required=default_required)
        # "none" breaks too (ANVIL-100 #17): a PR with no checks returns at once — polling until the
        # deadline can't make a missing CI appear.
        if result["state"] in {"success", "failure", "none"} or time.monotonic() >= deadline:
            break
        if stopped := _pause(min(poll_seconds, max(0.0, deadline - time.monotonic())), should_stop):
            if stopped == "cancelled":
                result["state"] = "cancelled"
            result["note"] = _stopped_note(stopped)
            return result
    if result["state"] == "none":
        result["note"] = ("No CI checks ran on this PR. This is not a pass: the PR's base branch may have no "
                          "CI, or CI did not start. Report it in the handoff instead of claiming CI is green.")
    if result["state"] == "failure":
        return _check_state(name, pr, with_logs=True, default_required=default_required)
    return result


def github_pr_state(repo: str, pr: int, wait_seconds: int = 0, poll_seconds: int = 30,
                    should_stop: StopCheck | None = None) -> dict[str, Any]:
    """PR state for the merge watch: open|merged|closed plus review and issue comments.

    With wait_seconds > 0, polls until the PR is no longer open or the wait expires. On 'merged'
    the runtime transitions the Jira ticket to Done; there is no webhook (a laptop has no URL).
    """
    name = full_name(repo)
    deadline = time.monotonic() + wait_seconds
    stopped = None
    while True:
        p = _gh("GET", f"/repos/{name}/pulls/{pr}")
        state = "merged" if p.get("merged_at") else p["state"]
        if state != "open" or time.monotonic() >= deadline:
            break
        stopped = _pause(min(poll_seconds, max(0.0, deadline - time.monotonic())), should_stop)
        if stopped:
            break
    comments = [
        {"author": (c.get("user") or {}).get("login"), "created": c.get("created_at"), "body": (c.get("body") or "")[:2000]}
        for c in _gh("GET", f"/repos/{name}/issues/{pr}/comments", params={"per_page": 100})
    ]
    reviews = [
        {"author": (r.get("user") or {}).get("login"), "state": r.get("state"), "body": (r.get("body") or "")[:2000], "submitted": r.get("submitted_at")}
        for r in _gh("GET", f"/repos/{name}/pulls/{pr}/reviews", params={"per_page": 100})
    ]
    out = {"pr": pr, "state": state, "merged_at": p.get("merged_at"), "merge_commit_sha": p.get("merge_commit_sha"),
           "head_sha": p["head"]["sha"], "draft": p.get("draft"), "title": p.get("title"), "url": p["html_url"],
           "comments": comments, "reviews": reviews}
    if stopped:
        out["note"] = _stopped_note(stopped)
    return out


def github_code_scanning_alerts(repo: str, pr: int | None = None, branch: str | None = None) -> list[dict[str, Any]]:
    """Open code-scanning (CodeQL) alerts on a PR, or on a branch when there is no PR (a review):
    rule, severity, message, file and line."""
    if (pr is None) == (branch is None):
        raise ToolError("give exactly one of pr or branch")
    ref = f"refs/pull/{pr}/head" if pr is not None else f"refs/heads/{branch}"
    alerts = _gh("GET", f"/repos/{full_name(repo)}/code-scanning/alerts", params={"ref": ref, "state": "open", "per_page": 100})
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


def github_merge(repo: str, pr: int, method: str = "squash",
                 required: list[str] | None = None,
                 work_branch: str | None = None, base: str | None = None) -> dict[str, Any]:
    """Merge a PR from an `anvil/...` branch. Refuses unless every check on the head commit is green.

    `required` is the case's [ci] required_checks for this run (ANVIL-98): when branch protection
    doesn't pin a set, only those checks gate the merge.
    `work_branch`/`base` bind the merge to this run's PR (ANVIL-98): the PR's head and base refs
    must match — an agent can't merge an arbitrary number.
    The run's merge policy (human / agent-after-approval / agent) is enforced by the runtime before this is offered.
    """
    name = full_name(repo)
    pull = _gh("GET", f"/repos/{name}/pulls/{pr}")
    head = pull["head"]["ref"]
    if work_branch and head != work_branch and not head.startswith(work_branch + "-"):
        # anvil/<key>-<slug> branches are this run's too; anvil/rtems-10 must not match rtems-1
        raise ToolError(f"refused: PR #{pr} is {head}, not this run's {work_branch} (bound to the run's PR)")
    if base and pull["base"]["ref"] != base:
        raise ToolError(f"refused: PR #{pr} targets {pull['base']['ref']}, not this run's base {base}")
    _require_agent_branch(head)
    files, page = [], 1
    while batch := _gh("GET", f"/repos/{name}/pulls/{pr}/files", params={"per_page": 100, "page": page}):
        files += [p for f in batch for p in (f["filename"], f.get("previous_filename") or "")]
        page += 1
    if blocked := sorted({p for p in files if is_protected(p)}):
        raise ToolError(f"refused: PR #{pr} changes protected paths ({', '.join(blocked[:10])}); agents may not change .github/")
    state = _check_state(name, pr, with_logs=False, default_required=required)
    if state["state"] != "success":
        raise ToolError(f"refused: checks on PR #{pr} are '{state['state']}' (failing: {', '.join(state['failing']) or 'none'})")
    r = _gh("PUT", f"/repos/{name}/pulls/{pr}/merge", json={"merge_method": method, "sha": state["head_sha"]})
    return {"pr": pr, "merged": r.get("merged", False), "sha": r.get("sha"), "message": r.get("message")}


GITHUB_TOOLS = [github_open_pr, github_checks, github_code_scanning_alerts, github_merge, github_pr_state]
