"""Run-scoped outward tools: what the run already knows, the agent does not get to choose.

A run has a repo, a base branch and (usually) a Jira ticket. The generic host tools take those as
arguments, and the agent used to supply them. That let it pick wrong values: rehearsal 6d9317d3
(ANVIL-32) opened its PR against `main` instead of the case base branch, so no CI ran, and on a
bug-replay mirror `main` holds the withheld fix. Here every outward tool is built for one run with
those values fixed; the agent supplies only what is genuinely its decision (branch name, titles,
text, PR number). Reads that are not tied to the run (jira_get, jira_search, confluence_*) stay free.
"""

from __future__ import annotations

from typing import Any

from anvil.tools import git_push as git_push_module
from anvil.tools import github, jira
from anvil.tools.errors import ToolError


def _case_required_checks(run: dict[str, Any]) -> list[str] | None:
    """The checks that gate this run's PR when its base branch has no protection: the case's
    [ci] required_checks (owner decision 2026-11-04: SonarCloud is advisory). None = every check gates."""
    from anvil.cases import case_for_run

    case = case_for_run(run)
    return list(case.required_checks) if case and case.required_checks else None


def run_tools(run: dict[str, Any], sandbox: Any) -> list[Any]:
    """The outward tools for one run: repo, base branch and ticket bound from the run record."""
    repo: str = run["repo"]
    base: str = run.get("base_ref") or "main"
    key: str | None = run.get("jira_key")
    required = _case_required_checks(run)
    push = git_push_module.make_git_push(sandbox)

    def git_push(branch: str) -> dict[str, Any]:
        """Push your committed `anvil/...` branch from the sandbox checkout to this run's GitHub repo.

        Commit first. Then call github_open_pr with the same branch.
        """
        return push(repo, branch)

    def github_open_pr(branch: str, title: str, body: str, draft: bool = False) -> dict[str, Any]:
        """Open a pull request from your pushed `anvil/...` branch into this run's base branch
        (or return the one already open). Call git_push first. Title must start with the Jira key."""
        return github.github_open_pr(repo, branch, title, body, base=base, draft=draft)

    def github_checks(pr: int, wait_seconds: int = 900, poll_seconds: int = 20) -> dict[str, Any]:
        """Wait (up to wait_seconds) for the CI checks on your PR to finish.

        Returns state success|failure|pending|none with every check, and log tails for failed jobs.
        `none` means no CI ran: that is not a pass.
        """
        return github.github_checks(repo, pr, wait_seconds=wait_seconds, poll_seconds=poll_seconds,
                                    default_required=required)

    def github_code_scanning_alerts(pr: int | None = None) -> list[dict[str, Any]]:
        """Open code-scanning alerts (CodeQL, cppcheck, ...) on your PR, to fix before review; without a
        PR, the alerts on this run's base branch (the input for a review, e.g. a CRA assessment)."""
        if pr is None:
            return github.github_code_scanning_alerts(repo, branch=base)
        return github.github_code_scanning_alerts(repo, pr)

    def github_pr_state(pr: int, wait_seconds: int = 0, poll_seconds: int = 30) -> dict[str, Any]:
        """Your PR's state (open|merged|closed) with review and issue comments; optionally wait for a change."""
        return github.github_pr_state(repo, pr, wait_seconds=wait_seconds, poll_seconds=poll_seconds)

    def github_merge(pr: int, method: str = "squash") -> dict[str, Any]:
        """Merge your PR (only when the run's merge policy allows it; always needs approval)."""
        return github.github_merge(repo, pr, method=method)

    from anvil.runtime.source import is_local

    # A local source (ANVIL-55) has no GitHub: no push, PR or CI tools. Its result is exported by the
    # host at the end of the run, so a local repo can never reach GitHub through the agent.
    github_tools: list[Any] = [] if is_local(repo) else [
        git_push, github_open_pr, github_checks, github_code_scanning_alerts, github_pr_state, github_merge]

    # A run whose ticket came from the local tracker (trigger "local:<key>") gets the same jira_*
    # names bound to the JSON store (ANVIL-56): the skills call jira_* and the interrupt_on gate on
    # jira_create is keyed by tool name, so a differently named create would bypass approval.
    if run.get("trigger", "").startswith("local:"):
        from anvil.tickets import LocalTracker

        tracker = LocalTracker.from_env()
        if tracker is None:
            raise ToolError("local ticket run but ANVIL_TICKETS_DIR is not set")

        def jira_get(key: str) -> dict[str, Any]:
            """Read a Jira issue: summary, status, type, labels, description and comments as plain text."""
            return tracker.get(key)

        def jira_search(jql: str, max_results: int = 20) -> list[dict[str, Any]]:
            """Find Jira issues with JQL. Returns key, summary, status, labels and description for each."""
            return tracker.search(jql, max_results)

        def jira_create(project: str, type: str, summary: str, description: str = "",
                        labels: list[str] | None = None) -> dict[str, str]:
            """Create a Jira issue (description in Markdown). Returns the new key and URL."""
            return tracker.create(project, type, summary, description, labels)

        jira_tools: list[Any] = [jira_get, jira_search, jira_create]
        _comment, _transition, _set_labels = tracker.comment, tracker.transition, tracker.set_labels
    else:
        jira_tools = [jira.jira_get, jira.jira_search, jira.jira_create]
        _comment, _transition, _set_labels = jira.jira_comment, jira.jira_transition, jira.jira_set_labels

    tools: list[Any] = [*github_tools, *jira_tools]
    if key:
        def jira_comment(markdown: str) -> dict[str, str]:
            """Comment on this run's Jira ticket (Markdown)."""
            return _comment(key, markdown)

        def jira_transition(to_status: str) -> dict[str, str]:
            """Move this run's Jira ticket to another status (e.g. "In Progress", "Done")."""
            return _transition(key, to_status)

        def jira_set_labels(add: list[str] | None = None, remove: list[str] | None = None) -> dict[str, Any]:
            """Add or remove labels on this run's Jira ticket (e.g. add ["anvil-in-review"])."""
            return _set_labels(key, add=add, remove=remove)

        tools += [jira_comment, jira_transition, jira_set_labels]
    return tools
    if key:
        def jira_comment(markdown: str) -> dict[str, str]:
            """Comment on this run's Jira ticket (Markdown)."""
            return jira.jira_comment(key, markdown)

        def jira_transition(to_status: str) -> dict[str, str]:
            """Move this run's Jira ticket to another status (e.g. "In Progress", "Done")."""
            return jira.jira_transition(key, to_status)

        def jira_set_labels(add: list[str] | None = None, remove: list[str] | None = None) -> dict[str, Any]:
            """Add or remove labels on this run's Jira ticket (e.g. add ["anvil-in-review"])."""
            return jira.jira_set_labels(key, add=add, remove=remove)

        tools += [jira_comment, jira_transition, jira_set_labels]
    return tools


def verify_outcome(run: dict[str, Any], sandbox: Any) -> list[str]:
    """Check a finished run's result on the host; return the problems (empty list = verified).

    The agent saying "done" is not evidence: rehearsal 6d9317d3 reported success with a PR that no CI
    ever ran on. A run counts as succeeded only if what it claims can be checked:

    * a PR from the sandbox's branch exists -> it targets the run's base branch and its CI passed;
    * the code changed but there is no PR -> not verified;
    * nothing changed (investigation, report) -> nothing to verify here;
    * and if the run replays a case, the case's own acceptance checks pass (ANVIL-44): a PR with green
      CI is necessary, not sufficient (TC2 run 7 bumped a submodule pointer and a version header).
    """
    import shlex

    from anvil.runtime.executor import repo_path

    from anvil.runtime.source import is_local

    base = run.get("base_ref") or "main"
    path = shlex.quote(repo_path(run["repo"]))
    if is_local(run["repo"]):
        return _local_problems(sandbox, path) + case_problems(run, sandbox)
    branch = sandbox.execute(f"git -C {path} rev-parse --abbrev-ref HEAD", timeout=60).output.strip()
    changed = sandbox.execute(
        # committed + tracked edits; untracked build leftovers (config.ini, build/) don't count
        f"cd {path} && {{ git diff --name-only origin/{shlex.quote(base)} HEAD; git diff --name-only HEAD; }}",
        timeout=60,
    ).output.strip()

    name = github.full_name(run["repo"])
    owner = name.split("/")[0]
    prs = github._gh("GET", f"/repos/{name}/pulls", params={"head": f"{owner}:{branch}", "state": "all"}) \
        if branch.startswith("anvil/") else []
    problems: list[str] = []
    if not prs:
        if changed:
            problems.append(f"the code changed on `{branch}` but no pull request was opened")
    else:
        problems += _pr_problems(run, prs[0], base)
    return problems + case_problems(run, sandbox)


def _local_problems(sandbox: Any, qpath: str) -> list[str]:
    """A local source has no PR or CI: what can be checked is that the work is committed (the export
    takes commits only) and, for a case replay, the case's acceptance checks (ANVIL-55)."""
    dirty = sandbox.execute(f"cd {qpath} && git status --porcelain --untracked-files=no", timeout=60).output.strip()
    return ["the agent left uncommitted changes to tracked files (only commits are exported)"] if dirty else []


def case_problems(run: dict[str, Any], sandbox: Any) -> list[str]:
    """The run's case acceptance checks (none when the run replays no case). Also used by dry runs."""
    from anvil.cases import case_for_run
    from anvil.cases.acceptance import run_acceptance
    from anvil.runtime.executor import repo_path

    case = case_for_run(run)
    return run_acceptance(case, sandbox, repo_path(run["repo"])) if case else []


def _pr_problems(run: dict[str, Any], pr: dict[str, Any], base: str) -> list[str]:
    problems = []
    if pr["base"]["ref"] != base:
        problems.append(f"PR #{pr['number']} targets `{pr['base']['ref']}`, not the run's base branch `{base}`")
    checks = github.github_checks(run["repo"], pr["number"], wait_seconds=0,
                                  default_required=_case_required_checks(run))
    if checks["state"] != "success":
        failing = ", ".join(checks.get("failing") or []) or "-"
        problems.append(f"CI on PR #{pr['number']} is `{checks['state']}` (failing: {failing}), not green")
    return problems
