"""Jira Cloud host tools (REST v3). Each function is a LangChain tool: type hints + docstring are its schema."""

from typing import Any

from anvil.tools._atlassian import ToolError, request, site
from anvil.tools.markdown import adf_to_text, to_adf

INTAKE_JQL = 'project = RTEMS AND labels = anvil AND statusCategory = "To Do" ORDER BY created ASC'
_FIELDS = ["summary", "status", "issuetype", "labels", "assignee", "description", "comment", "created", "updated"]


def _summarize(issue: dict, with_comments: bool = False) -> dict[str, Any]:
    f = issue.get("fields", {})
    out: dict[str, Any] = {
        "key": issue["key"],
        "url": f"{site()}/browse/{issue['key']}",
        "summary": f.get("summary"),
        "status": (f.get("status") or {}).get("name"),
        "type": (f.get("issuetype") or {}).get("name"),
        "labels": f.get("labels", []),
        "assignee": (f.get("assignee") or {}).get("displayName"),
        "description": adf_to_text(f.get("description")).strip(),
    }
    if with_comments:
        out["comments"] = [
            {"author": (c.get("author") or {}).get("displayName"), "created": c.get("created"), "body": adf_to_text(c.get("body")).strip()}
            for c in (f.get("comment") or {}).get("comments", [])
        ]
    return out


def jira_get(key: str) -> dict[str, Any]:
    """Read a Jira issue: summary, status, type, labels, description and comments as plain text."""
    return _summarize(request("GET", f"/rest/api/3/issue/{key}", params={"fields": ",".join(_FIELDS)}), with_comments=True)


def jira_search(jql: str, max_results: int = 20) -> list[dict[str, Any]]:
    """Find Jira issues with JQL. Returns key, summary, status, labels and description for each."""
    body = {"jql": jql, "maxResults": max_results, "fields": [f for f in _FIELDS if f != "comment"]}
    return [_summarize(i) for i in request("POST", "/rest/api/3/search/jql", json=body).get("issues", [])]


def jira_comment(key: str, markdown: str) -> dict[str, str]:
    """Post a comment (Markdown) on a Jira issue. Returns the comment id and URL."""
    c = request("POST", f"/rest/api/3/issue/{key}/comment", json={"body": to_adf(markdown)})
    return {"id": c["id"], "url": f"{site()}/browse/{key}?focusedCommentId={c['id']}"}


def jira_transition(key: str, to_status: str) -> dict[str, str]:
    """Move a Jira issue to a status by name, e.g. 'In Progress', 'In Review' or 'Done'."""
    transitions = request("GET", f"/rest/api/3/issue/{key}/transitions")["transitions"]
    wanted = to_status.casefold()
    match = next((t for t in transitions if wanted in (t["name"].casefold(), t["to"]["name"].casefold())), None)
    if match is None:
        names = ", ".join(sorted({t["to"]["name"] for t in transitions}))
        raise ToolError(f"{key} cannot move to '{to_status}'. Available: {names}")
    request("POST", f"/rest/api/3/issue/{key}/transitions", json={"transition": {"id": match["id"]}})
    return {"key": key, "status": match["to"]["name"]}


def jira_set_labels(key: str, add: list[str] | None = None, remove: list[str] | None = None) -> dict[str, Any]:
    """Add and/or remove labels on a Jira issue (labels carry agent state, e.g. anvil-in-review)."""
    ops = [{"add": label} for label in add or []] + [{"remove": label} for label in remove or []]
    if not ops:
        raise ToolError("jira_set_labels needs at least one label to add or remove")
    request("PUT", f"/rest/api/3/issue/{key}", json={"update": {"labels": ops}})
    return {"key": key, "added": add or [], "removed": remove or []}


def jira_create(project: str, type: str, summary: str, description: str = "", labels: list[str] | None = None) -> dict[str, str]:
    """Create a Jira issue (description in Markdown). Returns the new key and URL."""
    fields: dict[str, Any] = {"project": {"key": project}, "issuetype": {"name": type}, "summary": summary, "labels": labels or []}
    if description:
        fields["description"] = to_adf(description)
    key = request("POST", "/rest/api/3/issue", json={"fields": fields})["key"]
    return {"key": key, "url": f"{site()}/browse/{key}"}


def jira_intake_candidates(max_results: int = 20) -> list[dict[str, Any]]:
    """RTEMS issues labelled `anvil` and still To Do: what the intake poller should start runs for."""
    return jira_search(INTAKE_JQL, max_results)


JIRA_TOOLS = [jira_get, jira_search, jira_comment, jira_transition, jira_set_labels, jira_create]
