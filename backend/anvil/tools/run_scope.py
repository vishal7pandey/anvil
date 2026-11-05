"""Run-scoped outward tools: what the run already knows, the agent does not get to choose.

A run has a repo, a base branch and (usually) a Jira ticket. The generic host tools take those as
arguments, and the agent used to supply them. That let it pick wrong values: rehearsal 6d9317d3
(ANVIL-32) opened its PR against `main` instead of the case base branch, so no CI ran, and on a
bug-replay mirror `main` holds the withheld fix. Here every outward tool is built for one run with
those values fixed; the agent supplies only what is genuinely its decision (branch name, titles,
text, PR number). Reads that are not tied to the run (jira_get, jira_search, confluence_*) stay free.
"""

from __future__ import annotations

from anvil.runtime.sandbox import verify_run
import logging
import time
from typing import Any

log = logging.getLogger(__name__)

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

    def _stop() -> str | None:
        """Why a waiting tool must stop now: the run was cancelled, or its time budget ended (ANVIL-76)."""
        event = getattr(sandbox, "stop_requested", None)
        if event is not None and event.is_set():
            return "cancelled"
        deadline = getattr(sandbox, "deadline", None)
        if deadline is not None and time.monotonic() >= deadline:
            return "budget"
        return None

    def _budgeted(wait_seconds: int) -> int:
        deadline = getattr(sandbox, "deadline", None)
        return wait_seconds if deadline is None else max(0, min(wait_seconds, int(deadline - time.monotonic())))

    def github_checks(pr: int, wait_seconds: int = 900, poll_seconds: int = 20) -> dict[str, Any]:
        """Wait (up to wait_seconds) for the CI checks on your PR to finish.

        Returns state success|failure|pending|none with every check, and log tails for failed jobs.
        `none` means no CI ran: that is not a pass.
        """
        return github.github_checks(repo, pr, wait_seconds=_budgeted(wait_seconds), poll_seconds=poll_seconds,
                                    default_required=required, should_stop=_stop)

    def github_code_scanning_alerts(pr: int | None = None) -> list[dict[str, Any]]:
        """Open code-scanning alerts (CodeQL, cppcheck, ...) on your PR, to fix before review; without a
        PR, the alerts on this run's base branch (the input for a review, e.g. a CRA assessment)."""
        if pr is None:
            return github.github_code_scanning_alerts(repo, branch=base)
        return github.github_code_scanning_alerts(repo, pr)

    def github_pr_state(pr: int, wait_seconds: int = 0, poll_seconds: int = 30) -> dict[str, Any]:
        """Your PR's state (open|merged|closed) with review and issue comments; optionally wait for a change."""
        return github.github_pr_state(repo, pr, wait_seconds=_budgeted(wait_seconds), poll_seconds=poll_seconds,
                                      should_stop=_stop)

    # The run's work branch, stateless (run_tools rebuilds on resume: a remembered PR number would
    # die in the closure, ANVIL-98 review). Merge binds to the PR whose head is this branch.
    work = run.get("branch") or f"anvil/{(key or 'run-' + run['run_id'][:8]).lower()}"

    def github_merge(pr: int, method: str = "squash") -> dict[str, Any]:
        """Merge your PR (only when the run's merge policy allows it; always needs approval)."""
        return github.github_merge(repo, pr, method=method, required=required,
                                   work_branch=work, base=base)

    from anvil.runtime.source import is_local

    # A local source (ANVIL-55) has no GitHub: no push, PR or CI tools. Its result is exported by the
    # host at the end of the run, so a local repo can never reach GitHub through the agent.
    github_tools: list[Any] = [] if is_local(repo) else [
        git_push, github_open_pr, github_checks, github_code_scanning_alerts, github_pr_state, github_merge]

    # A run whose ticket came from the local tracker (trigger "local:<key>") gets the same jira_*
    # names bound to the JSON store (ANVIL-56): the skills call jira_* and the interrupt_on gate on
    # jira_create is keyed by tool name, so a differently named create would bypass approval.
    tracker = None
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
            _require_run_project(project, key)
            return tracker.create(project, type, summary, description, labels)

        jira_tools: list[Any] = [jira_get, jira_search, jira_create]
        _comment, _transition, _set_labels = tracker.comment, tracker.transition, tracker.set_labels
    else:
        def jira_create(project: str, type: str, summary: str, description: str = "",
                        labels: list[str] | None = None) -> dict[str, str]:
            """Create a Jira issue (description in Markdown). Returns the new key and URL."""
            _require_run_project(project, key)
            return jira.jira_create(project, type, summary, description, labels)

        jira_tools = [jira.jira_get, jira.jira_search, jira_create]
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

    tools += _knowledge_tools(run, tracker)
    return tools


def _require_run_project(project: str, key: str | None) -> None:
    """jira_create can only create inside this run's project — an agent can't be steered into
    opening tickets on another project (ANVIL-98)."""
    if key and project != key.split("-")[0]:
        raise ToolError(
            f"jira_create is bound to this run's project '{key.split('-')[0]}'; got '{project}'"
        )


def _require_run_ticket(ev: dict[str, Any], key: str | None) -> None:
    """publish_evidence's key must be this run's ticket — not another issue the agent names
    (ANVIL-98)."""
    if key and str(ev.get("key")) != key:
        raise ToolError(f"publish_evidence is bound to this run's ticket '{key}'; got '{ev.get('key')}'")


def _knowledge_tools(run: dict[str, Any], tracker: Any = None) -> list[Any]:
    """confluence_* + publish_evidence for this run (ANVIL-60/59).

    Local runs (local: trigger or a local repo) and any run on a laptop without Atlassian get the
    local knowledge folder: same tool names, pages as Markdown on disk, each indexed into the KB at
    once. Online runs keep the REST tools, but confluence_write/publish_evidence also ingest the
    page they just wrote so run 2's kb_search finds run 1's post-mortem without a reset.
    """
    from anvil.kb.local import LocalKnowledge
    from anvil.runtime.source import is_local
    from anvil.tools import confluence, evidence

    key: str | None = run.get("jira_key")
    from anvil.tools._atlassian import AtlassianSettings

    def _atlassian_configured() -> bool:
        s = AtlassianSettings()
        return bool(s.atlassian_email and s.atlassian_api_token)

    def _index(source: str, external_id: str, title: str, url: str | None, text: str) -> bool:
        try:
            from datetime import datetime, timezone

            from anvil.kb.sources import SourceDocument
            from anvil.kb.tool import get_store

            report = get_store().ingest([
                SourceDocument(source=source, external_id=external_id, title=title,
                               url=url, text=text,
                               metadata={"created_at": datetime.now(timezone.utc)
                                         .strftime("%Y-%m-%dT%H:%M:%SZ")})])
            return (report.documents_added + report.documents_updated) > 0
        except Exception as exc:  # noqa: BLE001 - KB may be down; the page write already succeeded
            log.warning("KB write-back for %s %s failed: %s", source, external_id, exc)
            return False

    local_run = is_local(run["repo"]) or (run.get("trigger") or "").startswith("local:")
    use_local = local_run or not _atlassian_configured()
    log.info("run %s: knowledge tools -> %s", run.get("run_id", "?"),
             "local-folder" if use_local else "confluence")
    if use_local:
        knowledge = LocalKnowledge()

        def confluence_search(cql: str, limit: int = 10) -> list[dict[str, Any]]:
            """Search local knowledge pages (the laptop has no Confluence)."""
            return knowledge.search(cql, limit)

        def confluence_get(page_id: str) -> dict[str, Any]:
            """Read a local knowledge page by id (its folder-relative path)."""
            return knowledge.get(page_id)

        def confluence_write(space_key: str, title: str, markdown_or_storage: str,
                             parent_id: str | None = None) -> dict[str, Any]:
            """Write a page to the local knowledge folder (no Atlassian); indexed for kb_search."""
            return knowledge.write_page(space_key, title, markdown_or_storage, parent_id)

        def publish_evidence(ev: dict[str, Any], space: str = "RTKB",
                             parent_id: str | None = None) -> dict[str, Any]:
            """Write the evidence package to the local knowledge folder + ticket (no Atlassian)."""
            if space.upper() not in evidence.EVIDENCE_SPACES:
                from anvil.tools.errors import ToolError

                raise ToolError(f"evidence pages go to {sorted(evidence.EVIDENCE_SPACES)}, not '{space}'")
            _require_run_ticket(ev, key)
            title, md = evidence.confluence_evidence(ev)
            page = knowledge.write_page(space, title, md, parent_id)
            result: dict[str, Any] = {"confluence_page": page, "jira_comment": None, "labels": None}
            if tracker is not None:
                ev2 = {**ev, "evidence_url": page["url"]}
                result["jira_comment"] = tracker.comment(
                    ev["key"], evidence.jira_evidence_comment(ev2))
                result["labels"] = tracker.set_labels(ev["key"], add=["anvil-in-review"])
            return result

        return [confluence_search, confluence_get, confluence_write, publish_evidence]

    def confluence_write(space_key: str, title: str, markdown_or_storage: str,
                         parent_id: str | None = None) -> dict[str, Any]:
        """Write a Confluence page (create or update by title) and index it for kb_search at once."""
        page = confluence.confluence_write(space_key, title, markdown_or_storage, parent_id)
        _index("confluence", str(page["id"]), page["title"], page["url"], markdown_or_storage)
        return page

    def publish_evidence(ev: dict[str, Any], space: str = "RTKB",
                         parent_id: str | None = None) -> dict[str, Any]:
        """Write the evidence package (RTKB page + Jira comment + label), indexed for kb_search."""
        _require_run_ticket(ev, key)
        result = evidence.publish_evidence(ev, space, parent_id)
        title, md = evidence.confluence_evidence(ev)
        page = result.get("confluence_page") or {}
        _index("confluence", str(page.get("id")), title, page.get("url"), md)
        return result

    return [confluence.confluence_search, confluence.confluence_get, confluence_write,
            publish_evidence]
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
    # Hardened path (ANVIL-99): the agent's shell startup files and PATH cannot change these answers, and a
    # failed git command is a failed verification, never "nothing changed".
    reads = {
        "the branch": verify_run(sandbox, f"git -C {path} rev-parse --abbrev-ref HEAD", timeout=60),
        "the last commit": verify_run(sandbox, f"git -C {path} rev-parse HEAD", timeout=60),
        # committed + tracked edits; untracked build leftovers (config.ini, build/) don't count
        "the changed files": verify_run(
            sandbox,
            f"cd {path} && git diff --name-only origin/{shlex.quote(base)} HEAD && git diff --name-only HEAD",
            timeout=60),
    }
    unreadable = [f"could not verify the run: reading {what} failed (git exit {r.exit_code}): "
                  f"{(r.output or '').strip()[-200:] or 'no output'}"
                  for what, r in reads.items() if r.exit_code != 0]
    if unreadable:
        return unreadable
    branch = reads["the branch"].output.strip()
    head_sha = reads["the last commit"].output.strip()
    changed = reads["the changed files"].output.strip()

    name = github.full_name(run["repo"])
    owner = name.split("/")[0]
    prs = github._gh("GET", f"/repos/{name}/pulls", params={"head": f"{owner}:{branch}", "state": "all"}) \
        if branch.startswith("anvil/") else []
    problems: list[str] = []
    if not prs:
        if changed:
            problems.append(f"the code changed on `{branch}` but no pull request was opened")
    else:
        # An open PR whose head is the run's last commit; closed or stale PRs for the same branch name
        # (state=all returns them newest first) must not decide the verdict.
        open_prs = [p for p in prs if p.get("state", "open") == "open"] or prs
        pr = next((p for p in open_prs if (p.get("head") or {}).get("sha") == head_sha), open_prs[0])
        pr_sha = (pr.get("head") or {}).get("sha")
        if pr_sha and head_sha and pr_sha != head_sha:
            problems.append(f"PR #{pr['number']} points at {pr_sha[:7]}, not the run's last commit "
                            f"{head_sha[:7]}: later work was never pushed")
        problems += _pr_problems(run, pr, base)
    return problems + case_problems(run, sandbox)


def _local_problems(sandbox: Any, qpath: str) -> list[str]:
    """A local source has no PR or CI: what can be checked is that the work is committed (the export
    takes commits only) and, for a case replay, the case's acceptance checks (ANVIL-55)."""
    res = verify_run(sandbox, f"cd {qpath} && git status --porcelain --untracked-files=no", timeout=60)
    if res.exit_code != 0:
        return [f"could not verify the run: git status failed (git exit {res.exit_code}): "
                f"{(res.output or '').strip()[-200:] or 'no output'}"]
    dirty = res.output.strip()
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
