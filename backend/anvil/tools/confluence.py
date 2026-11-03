"""Confluence Cloud host tools. Pages via REST v2; CQL search via v1 (v2 has no search endpoint)."""

from typing import Any

from anvil.tools._atlassian import AtlassianSettings, ToolError, request, site
from anvil.tools.markdown import to_storage


def _page_url(links: dict) -> str:
    return f"{site()}/wiki{links.get('webui', '')}"


def confluence_search(cql: str, limit: int = 10) -> list[dict[str, Any]]:
    """Search Confluence with CQL, e.g. 'space = RTKB AND text ~ "interrupt"'. Returns id, title, space, excerpt, url."""
    data = request("GET", "/wiki/rest/api/search", params={"cql": cql, "limit": limit})
    return [
        {
            "id": (r.get("content") or {}).get("id"),
            "title": r.get("title") or (r.get("content") or {}).get("title"),
            "space": ((r.get("resultGlobalContainer") or {}).get("title")),
            "excerpt": r.get("excerpt", ""),
            "url": f"{site()}/wiki{r.get('url', '')}",
        }
        for r in data.get("results", [])
    ]


def confluence_get(page_id: str) -> dict[str, Any]:
    """Read a Confluence page: title, version and body in storage format (XHTML)."""
    p = request("GET", f"/wiki/api/v2/pages/{page_id}", params={"body-format": "storage"})
    return {
        "id": p["id"],
        "title": p["title"],
        "version": p["version"]["number"],
        "body": p["body"]["storage"]["value"],
        "url": _page_url(p.get("_links", {})),
    }


def _space_id(space_key: str) -> str:
    spaces = request("GET", "/wiki/api/v2/spaces", params={"keys": space_key}).get("results", [])
    if not spaces:
        raise ToolError(f"Confluence space '{space_key}' not found or not visible to this token")
    return spaces[0]["id"]


def confluence_write(space_key: str, title: str, markdown_or_storage: str, parent_id: str | None = None) -> dict[str, Any]:
    """Create a Confluence page, or update the page with the same title in that space.

    The body is Markdown, or storage-format XHTML if it starts with '<'. Returns id, version and url.
    """
    allowed = {k.strip().upper() for k in AtlassianSettings().anvil_confluence_write_spaces.split(",") if k.strip()}
    if space_key.upper() not in allowed:
        raise ToolError(f"refused: agents may only write to Confluence spaces {sorted(allowed)}, not '{space_key}'")
    space_id = _space_id(space_key)
    text = markdown_or_storage.strip()
    body = {"representation": "storage", "value": text if text.startswith("<") else to_storage(text)}
    existing = request("GET", "/wiki/api/v2/pages", params={"space-id": space_id, "title": title}).get("results", [])
    if existing:
        page = existing[0]
        payload = {"id": page["id"], "status": "current", "title": title, "body": body, "version": {"number": page["version"]["number"] + 1}}
        if parent_id:
            payload["parentId"] = parent_id
        p = request("PUT", f"/wiki/api/v2/pages/{page['id']}", json=payload)
    else:
        payload = {"spaceId": space_id, "status": "current", "title": title, "body": body}
        if parent_id:
            payload["parentId"] = parent_id
        p = request("POST", "/wiki/api/v2/pages", json=payload)
    return {"id": p["id"], "title": p["title"], "version": p["version"]["number"], "url": _page_url(p.get("_links", {}))}


CONFLUENCE_TOOLS = [confluence_search, confluence_get, confluence_write]
