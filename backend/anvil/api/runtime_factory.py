import asyncio
import logging
import os
import sys
from typing import Any

from sqlalchemy.engine import make_url

from anvil.api.config import get_settings, normalize_database_url
from anvil.api.executor import RunExecutor

logger = logging.getLogger(__name__)


def _runtime_database_url(database_url: str) -> str:
    normalized_url = normalize_database_url(database_url)
    url = make_url(normalized_url)
    if url.drivername == "postgresql+psycopg":
        return url.set(drivername="postgresql").render_as_string(hide_password=False)
    return normalized_url


def _validate_async_postgres_event_loop() -> None:
    if sys.platform != "win32":
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if isinstance(loop, asyncio.ProactorEventLoop):
        raise RuntimeError(
            "ANVIL_EXECUTOR=deepagent requires a Windows SelectorEventLoop for Psycopg; "
            "start the API with `python -m anvil.api.serve`"
        )


def create_executor_from_environment() -> RunExecutor | None:
    executor_name = os.environ.get("ANVIL_EXECUTOR", "").strip().casefold()
    if not executor_name:
        return None
    if executor_name != "deepagent":
        raise ValueError(f"Unsupported ANVIL_EXECUTOR value: {executor_name}")

    _validate_async_postgres_event_loop()

    from anvil.cases import case_for_run
    from anvil.kb.tool import make_kb_search
    from anvil.runtime.executor import DeepAgentExecutor
    from anvil.runtime.websearch import TC_BLOCKLIST, env_allowlist, make_web_search
    from anvil.tools.errors import returning_errors
    from anvil.tools.evidence import handoff_note
    from anvil.tools.git_push import make_git_push
    from anvil.tools.github import github_open_pr
    from anvil.tools.jira import jira_comment, jira_set_labels
    from anvil.tools.lint import make_lint_changed
    from anvil.tools.vendor import make_vendor_reimport
    from anvil.tools.run_scope import run_tools, verify_outcome

    def _case_as_of(run: dict[str, Any]) -> str | None:
        """The leak boundary for this run's case (ANVIL-43), or None for ordinary runs."""
        case = case_for_run(run)
        return case.as_of if case else None

    def _web_allowlist(run: dict[str, Any]) -> tuple[str, ...]:
        """Trusted web_search sources: the run's case decides, else ANVIL_WEB_ALLOWLIST (ANVIL-64).
        Host-side only; the agent never chooses its own sources."""
        case = case_for_run(run)
        return tuple(case.web_allow) if case and case.web_allow else env_allowlist()

    def tools_factory(sandbox: Any, run: dict[str, Any]) -> list[Any]:
        tools = [
            # Repo, base branch and ticket come from the run, never from the agent (ANVIL-32).
            # run_tools also binds confluence_*/publish_evidence per-run: the local knowledge
            # folder when the run is local or Atlassian is unset (ANVIL-60/59).
            *run_tools(run, sandbox),
            make_web_search(blocklist=TC_BLOCKLIST, allowlist=_web_allowlist(run), run_id=run["run_id"]),
            make_kb_search(_case_as_of(run)),
            # The one way to run pre-commit: changed files only (ANVIL-74).
            make_lint_changed(run, sandbox),
            # The mechanical part of a vendored-component upgrade: three-way merge, agent resolves
            # only the true conflicts (ANVIL-82).
            make_vendor_reimport(run, sandbox),
        ]
        # A ToolError goes back to the model instead of failing the run (ANVIL-19, run c820a577).
        return [returning_errors(t) if callable(t) else t for t in tools]

    def push_hook(sandbox: Any, run: dict[str, Any], wip_branch: str) -> str:
        pushed = make_git_push(sandbox)(run["repo"], wip_branch)
        # A pushed branch gets no CI; a draft PR does (build, tests, SBOM/OSV on the mirrors), so the
        # human picking up the handoff sees the same evidence a finished run would have. Best effort:
        # without it the note still links the branch.
        key = run.get("jira_key") or "ANVIL"
        try:
            pr = github_open_pr(
                run["repo"],
                wip_branch,
                f"{key}: WIP handoff (Anvil run {run.get('run_id', '?')})",
                "Anvil stopped and handed this task to a human. This draft PR exists so CI runs on "
                f"the work in progress; the handoff note is on {key}. Do not merge as is.",
                base=run.get("base_ref") or "main",
                draft=True,
            )
        except Exception as exc:  # noqa: BLE001 - the handoff must complete without it
            logger.warning("Draft PR for %s failed: %s", wip_branch, exc)
            return pushed["url"]
        return pr["url"]

    def report_hook(run: dict[str, Any], handoff: dict[str, Any]) -> None:
        jira_key = run.get("jira_key")
        if not isinstance(jira_key, str) or not jira_key:
            return
        note = handoff.get("note")
        if isinstance(note, dict):
            comment_run = {**run, "branch": handoff.get("wip_branch")}
            markdown = handoff_note(comment_run, note)
            wip_branch = handoff.get("wip_branch")
            url = handoff.get("url")
            sandbox = handoff.get("sandbox")
            details = []
            if wip_branch:
                details.append(f"**WIP branch:** `{wip_branch}`")
            if url:
                details.append(f"**WIP branch URL:** {url}")
            if sandbox:
                details.append(f"**Sandbox:** `{sandbox}`")
            if details:
                markdown = f"{markdown}\n\n" + "\n".join(details)
        else:
            markdown = handoff.get("markdown")
            if not isinstance(markdown, str) or not markdown:
                raise ValueError("Handoff report requires a structured note or Markdown fallback")
        if run.get("trigger", "").startswith("local:"):
            # The handoff note goes into the ticket JSON, not real Jira (ANVIL-56); the patch
            # path from the local export rides in `url`/details as usual.
            from anvil.tickets import LocalTracker

            tracker = LocalTracker.from_env()
            if tracker is not None:
                tracker.comment(jira_key, markdown)
                tracker.set_labels(jira_key, add=["anvil-needs-human"])
                return
        jira_comment(jira_key, markdown)
        jira_set_labels(jira_key, add=["anvil-needs-human"])

    settings = get_settings()
    import inspect
    executor_kwargs: dict[str, Any] = {
        "db_url": _runtime_database_url(settings.database_dsn),
        "tools_factory": tools_factory,
        "push_hook": push_hook,
        "report_hook": report_hook,
        "verify_hook": verify_outcome,
    }
    if "owner_id" in inspect.signature(DeepAgentExecutor.__init__).parameters:
        executor_kwargs["owner_id"] = settings.owner_id
    return DeepAgentExecutor(**executor_kwargs)
