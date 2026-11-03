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

    from anvil.runtime.executor import DeepAgentExecutor
    from anvil.runtime.websearch import TC_BLOCKLIST, make_web_search
    from anvil.tools.confluence import CONFLUENCE_TOOLS
    from anvil.tools.evidence import handoff_note, publish_evidence
    from anvil.tools.git_push import make_git_push
    from anvil.tools.github import GITHUB_TOOLS, github_open_pr
    from anvil.tools.jira import JIRA_TOOLS, jira_comment, jira_set_labels

    def tools_factory(sandbox: Any, run: dict[str, Any]) -> list[Any]:
        return [
            *JIRA_TOOLS,
            *GITHUB_TOOLS,
            *CONFLUENCE_TOOLS,
            publish_evidence,
            make_git_push(sandbox),
            make_web_search(blocklist=TC_BLOCKLIST, run_id=run["run_id"]),
        ]

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
        jira_comment(jira_key, markdown)
        jira_set_labels(jira_key, add=["anvil-needs-human"])

    return DeepAgentExecutor(
        db_url=_runtime_database_url(get_settings().database_dsn),
        tools_factory=tools_factory,
        push_hook=push_hook,
        report_hook=report_hook,
    )
