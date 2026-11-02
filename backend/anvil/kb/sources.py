"""Knowledge sources: the rtems-docs mirror and closed RTEMS GitLab issues.

Both loaders return plain ``SourceDocument`` values; embedding, storage and
exclusion happen in ``store.py``. Loaders are pure enough to unit-test: the
GitLab one takes an ``httpx.Client`` and the docs one reads a local checkout
(the CLI can clone the mirror for you).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from .config import KBSettings
from .exclusions import GITLAB_ISSUE_SOURCE, ExclusionGuard

log = logging.getLogger(__name__)

DOCS_SOURCE = "rtems-docs"
DOC_SUFFIXES = {".md", ".rst", ".txt"}
MAX_DOC_BYTES = 512 * 1024


@dataclass(frozen=True)
class SourceDocument:
    source: str
    external_id: str
    title: str
    url: str | None
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


def documents_from_docs_dir(
    root: Path, repo_url: str | None = None
) -> Iterator[SourceDocument]:
    root = root.resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"docs directory not found: {root}")
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in DOC_SUFFIXES:
            continue
        relative = path.relative_to(root)
        if any(part.startswith(".") or part == "build" for part in relative.parts):
            continue
        if path.stat().st_size > MAX_DOC_BYTES:
            log.info("skipping oversized docs file %s", relative)
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="utf-8", errors="replace")
        if not text.strip():
            continue
        rel_posix = relative.as_posix()
        yield SourceDocument(
            source=DOCS_SOURCE,
            external_id=rel_posix,
            title=_title_from_text(text, path.name),
            url=f"{repo_url.rstrip('/')}/blob/main/{rel_posix}" if repo_url else None,
            text=text,
            metadata={"path": rel_posix},
        )


def fetch_closed_gitlab_issues(
    settings: KBSettings,
    *,
    client: httpx.Client | None = None,
    guard: ExclusionGuard | None = None,
    max_pages: int | None = None,
    per_page: int = 100,
) -> list[SourceDocument]:
    guard = guard or ExclusionGuard(settings.excluded_issue_ids)
    owns_client = client is None
    client = client or httpx.Client(timeout=60.0)
    headers = {"Accept": "application/json"}
    if settings.gitlab_token:
        headers["PRIVATE-TOKEN"] = settings.gitlab_token
    project = quote(settings.gitlab_project, safe="")
    base = f"{settings.gitlab_url.rstrip('/')}/api/v4/projects/{project}/issues"
    documents: list[SourceDocument] = []
    skipped = 0
    try:
        page = 1
        while True:
            response = client.get(
                base,
                params={
                    "state": "closed",
                    "scope": "all",
                    "per_page": per_page,
                    "page": page,
                    "order_by": "updated_at",
                    "sort": "desc",
                },
                headers=headers,
            )
            response.raise_for_status()
            issues = response.json()
            if not issues:
                break
            for issue in issues:
                iid = str(issue.get("iid", issue.get("id")))
                if guard.is_excluded(GITLAB_ISSUE_SOURCE, iid):
                    skipped += 1
                    continue
                text = _issue_text(issue)
                if not text.strip():
                    continue
                documents.append(
                    SourceDocument(
                        source=GITLAB_ISSUE_SOURCE,
                        external_id=iid,
                        title=str(issue.get("title") or f"RTEMS issue #{iid}"),
                        url=issue.get("web_url"),
                        text=text,
                        metadata={
                            "labels": issue.get("labels", []),
                            "closed_at": issue.get("closed_at"),
                            "updated_at": issue.get("updated_at"),
                        },
                    )
                )
            if max_pages is not None and page >= max_pages:
                break
            if len(issues) < per_page:
                break
            page += 1
    finally:
        if owns_client:
            client.close()
    if skipped:
        log.info("excluded %d TC1 issue(s) from ingestion", skipped)
    return documents


def _issue_text(issue: dict[str, Any]) -> str:
    labels = ", ".join(issue.get("labels") or [])
    parts = [
        f"# {issue.get('title', '').strip()}",
        f"Issue #{issue.get('iid')} in the RTEMS tracker. State: closed. Labels: {labels or 'none'}.",
        (issue.get("description") or "").strip(),
    ]
    return "\n\n".join(part for part in parts if part.strip())


_METADATA_PREFIXES = ("%", "..", ":", "<!--", "|")


def _title_from_text(text: str, fallback: str) -> str:
    lines = [line.strip() for line in text.splitlines()]
    for line in lines:
        if line.startswith("#") and line.lstrip("#").strip():
            return line.lstrip("#").strip()[:120]
    for line in lines:
        if not line or line.startswith(_METADATA_PREFIXES):
            continue
        return line[:120]
    return fallback
