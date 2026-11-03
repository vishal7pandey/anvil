import os
from typing import Any

from anvil.api.executor import RunExecutor


def create_executor_from_environment() -> RunExecutor | None:
    executor_name = os.environ.get("ANVIL_EXECUTOR", "").strip().casefold()
    if not executor_name:
        return None
    if executor_name != "deepagent":
        raise ValueError(f"Unsupported ANVIL_EXECUTOR value: {executor_name}")

    from anvil.runtime.executor import DeepAgentExecutor
    from anvil.runtime.websearch import TC_BLOCKLIST, make_web_search
    from anvil.tools.confluence import CONFLUENCE_TOOLS
    from anvil.tools.evidence import handoff_note, publish_evidence
    from anvil.tools.git_push import make_git_push
    from anvil.tools.github import GITHUB_TOOLS
    from anvil.tools.jira import JIRA_TOOLS, jira_comment, jira_set_labels

    def tools_factory(sandbox: Any, run: dict[str, Any]) -> list[Any]:
        blocklist = TC_BLOCKLIST if run.get("jira_key") else ()
        return [
            *JIRA_TOOLS,
            *GITHUB_TOOLS,
            *CONFLUENCE_TOOLS,
            publish_evidence,
            make_git_push(sandbox),
            make_web_search(blocklist=blocklist, run_id=run["run_id"]),
        ]

    def push_hook(sandbox: Any, run: dict[str, Any], wip_branch: str) -> str:
        pushed = make_git_push(sandbox)(run["repo"], wip_branch)
        return pushed["url"]

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
        tools_factory=tools_factory,
        push_hook=push_hook,
        report_hook=report_hook,
    )
