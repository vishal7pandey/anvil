"""GitLab issues and merge requests, with their discussion, as KB documents (ANVIL-39).

Generic over GitLab instances and projects: the project list comes from config
(``KB_GITLAB_PROJECTS``), nothing here knows about RTEMS. One document per issue / MR:
title, state, labels, description and every human comment (system notes such as
"changed the label" are dropped).

Built for a slow, polite crawl of a public instance (gitlab.rtems.org allows 60
anonymous requests a minute):

* requests are paced (``min_interval``) and a 429 waits for ``Retry-After``;
* comments are fetched only when ``user_notes_count`` > 0;
* items are walked oldest-update first with ``updated_after``, and the caller gets a
  cursor after every page, so an interrupted crawl resumes where it stopped.

The leak guard (withheld issues / MRs / fix commits) is applied by the caller and by
``KBStore.ingest``; this module only produces documents.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import quote

import httpx

from .exclusions import GITLAB_ISSUE_SOURCE, GITLAB_MR_SOURCE
from .sources import SourceDocument

log = logging.getLogger(__name__)

Kind = Literal["issues", "merge_requests"]


def issue_external_id(project: str, iid: int | str) -> str:
    return f"{project}#{iid}"


def mr_external_id(project: str, iid: int | str) -> str:
    return f"{project}!{iid}"


@dataclass
class GitLabClient:
    """Paced GET against the GitLab REST API v4."""

    base_url: str
    token: str | None = None
    client: httpx.Client | None = None
    min_interval: float = 1.05  # seconds between requests: 60/min anonymous limit
    max_retries: int = 5
    sleep: Callable[[float], None] = time.sleep
    _last: float = 0.0

    def __post_init__(self) -> None:
        self._owns = self.client is None
        self.client = self.client or httpx.Client(timeout=60.0, follow_redirects=True)
        self.headers = {"Accept": "application/json", "User-Agent": "anvil-kb"}
        if self.token:
            self.headers["PRIVATE-TOKEN"] = self.token

    def close(self) -> None:
        if self._owns and self.client is not None:
            self.client.close()

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url.rstrip('/')}/api/v4/{path.lstrip('/')}"
        return self._request("GET", url, path, params=params)

    def graphql(self, query: str, variables: dict[str, Any]) -> Any:
        url = f"{self.base_url.rstrip('/')}/api/graphql"
        body = self._request("POST", url, "graphql", json={"query": query, "variables": variables})
        if body.get("errors"):
            raise RuntimeError(f"GitLab GraphQL error: {body['errors']}")
        return body["data"]

    def _request(self, method: str, url: str, label: str, **kwargs: Any) -> Any:
        assert self.client is not None
        for attempt in range(self.max_retries + 1):
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                self.sleep(wait)
            self._last = time.monotonic()
            response = self.client.request(method, url, headers=self.headers, **kwargs)
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == self.max_retries:
                    response.raise_for_status()
                retry = float(response.headers.get("Retry-After") or 2 ** (attempt + 1))
                log.warning("GitLab %s on %s, retrying in %.0fs", response.status_code, label, retry)
                self.sleep(retry)
                continue
            response.raise_for_status()
            return response.json()
        raise RuntimeError("unreachable")


def fetch_gitlab_items(
    gl: GitLabClient,
    project: str,
    kind: Kind,
    *,
    updated_after: str | None = None,
    with_notes: bool = True,
    per_page: int = 100,
    max_pages: int | None = None,
    on_page: Callable[[str], None] | None = None,
) -> Iterator[SourceDocument]:
    """Yield one document per issue or MR of ``project``, oldest update first.

    ``on_page(cursor)`` is called after each page with the newest ``updated_at`` seen,
    so the caller can persist it and pass it back as ``updated_after`` next time.
    """
    encoded = quote(project, safe="")
    page = 1
    while True:
        params: dict[str, Any] = {
            "state": "all", "scope": "all", "per_page": per_page, "page": page,
            "order_by": "updated_at", "sort": "asc",
        }
        if updated_after:
            params["updated_after"] = updated_after
        items = gl.get(f"projects/{encoded}/{kind}", params)
        if not items:
            break
        cursor = updated_after
        for item in items:
            notes: list[dict[str, Any]] = []
            if with_notes and int(item.get("user_notes_count") or 0) > 0:
                notes = _fetch_notes(gl, project, kind, item["iid"])
            document = item_document(project, kind, item, notes)
            if document is not None:
                yield document
            cursor = item.get("updated_at") or cursor
        if on_page and cursor:
            on_page(cursor)
        if len(items) < per_page or (max_pages is not None and page >= max_pages):
            break
        page += 1


_NOTES_QUERY = """
query($path: ID!, $iid: String!, $after: String) {
  project(fullPath: $path) {
    %s(iid: $iid) {
      notes(first: 100, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes { body system createdAt author { username } }
      }
    }
  }
}
"""


def _fetch_notes(gl: GitLabClient, project: str, kind: Kind, iid: int) -> list[dict[str, Any]]:
    """Comments via GraphQL: gitlab.rtems.org answers REST /notes with 401 for anonymous
    clients, while the same public comments are readable through GraphQL."""
    field = "mergeRequest" if kind == "merge_requests" else "issue"
    query = _NOTES_QUERY % field
    notes: list[dict[str, Any]] = []
    after = None
    while True:
        data = gl.graphql(query, {"path": project, "iid": str(iid), "after": after})
        item = ((data or {}).get("project") or {}).get(field)
        if not item:
            return notes
        conn = item["notes"]
        for n in conn["nodes"]:
            notes.append({"body": n.get("body"), "system": n.get("system"),
                          "created_at": n.get("createdAt"), "author": n.get("author") or {}})
        if not conn["pageInfo"]["hasNextPage"]:
            return notes
        after = conn["pageInfo"]["endCursor"]


def item_document(project: str, kind: Kind, item: dict[str, Any],
                  notes: list[dict[str, Any]]) -> SourceDocument | None:
    iid = item.get("iid")
    title = str(item.get("title") or "").strip()
    is_mr = kind == "merge_requests"
    labels = ", ".join(item.get("labels") or []) or "none"
    head = [f"# {title}"]
    if is_mr:
        head.append(
            f"Merge request !{iid} in {project}. State: {item.get('state')}. "
            f"{item.get('source_branch')} -> {item.get('target_branch')}. Labels: {labels}. "
            f"Created {_day(item.get('created_at'))}"
            + (f", merged {_day(item.get('merged_at'))}." if item.get("merged_at") else ".")
        )
    else:
        head.append(
            f"Issue #{iid} in {project}. State: {item.get('state')}. Labels: {labels}. "
            f"Created {_day(item.get('created_at'))}"
            + (f", closed {_day(item.get('closed_at'))}." if item.get("closed_at") else ".")
        )
    parts = ["\n".join(head), (item.get("description") or "").strip()]
    comments = [n for n in notes if not n.get("system") and (n.get("body") or "").strip()]
    if comments:
        parts.append("## Discussion")
        for note in comments:
            author = (note.get("author") or {}).get("username", "unknown")
            parts.append(f"**{author}** ({_day(note.get('created_at'))}):\n{note['body'].strip()}")
    text = "\n\n".join(p for p in parts if p.strip())
    if not title and not text.strip():
        return None
    return SourceDocument(
        source=GITLAB_MR_SOURCE if is_mr else GITLAB_ISSUE_SOURCE,
        external_id=(mr_external_id if is_mr else issue_external_id)(project, iid),
        title=title or f"{project} {'!' if is_mr else '#'}{iid}",
        url=item.get("web_url"),
        text=text,
        metadata={
            "project": project,
            "iid": iid,
            "state": item.get("state"),
            "labels": item.get("labels") or [],
            "created_at": item.get("created_at"),
            "updated_at": item.get("updated_at"),
            "closed_at": item.get("closed_at") or item.get("merged_at"),
            "comments": len(comments),
        },
    )


def _day(ts: str | None) -> str:
    return (ts or "unknown")[:10]
