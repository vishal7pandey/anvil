"""Knowledge sources: rtems-docs mirror, RTEMS GitLab issues, and Confluence wiki.

Loaders return plain ``SourceDocument`` values; embedding, storage and
exclusion happen in ``store.py``. Loaders are pure enough to unit-test: the
GitLab one takes an ``httpx.Client``, Confluence takes optional callables,
and docs reads a local checkout (the CLI can clone the mirror for you).
"""

from __future__ import annotations

import html
from html.parser import HTMLParser
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import subprocess
from typing import Any
from urllib.parse import quote

import httpx

from .config import KBSettings
from .exclusions import GITLAB_ISSUE_SOURCE, ExclusionGuard

log = logging.getLogger(__name__)

DOCS_SOURCE = "rtems-docs"
CONFLUENCE_SOURCE = "confluence"
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


def _git_commit_dates_for_dir(root: Path) -> dict[str, str]:
    """Map relative posix path inside root -> latest ISO committer date from git history.

    Leaves files undated if the repository is a shallow clone to avoid stamping all
    files with the shallow tip commit date (ANVIL-68).
    """
    dates: dict[str, str] = {}
    root = root.resolve()
    try:
        top_res = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if top_res.returncode != 0:
            return dates
        repo_root = Path(top_res.stdout.strip()).resolve()

        shallow_res = subprocess.run(
            ["git", "rev-parse", "--is-shallow-repository"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if shallow_res.returncode == 0 and shallow_res.stdout.strip().lower() == "true":
            log.warning(
                "git repository at %s is shallow; leaving documents undated to avoid tip date pollution",
                repo_root,
            )
            return dates

        res = subprocess.run(
            ["git", "log", "--name-only", "--format=COMMIT:%cI", "--", str(root)],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode == 0:
            current_date: str | None = None
            for line in res.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                if line.startswith("COMMIT:"):
                    current_date = line[len("COMMIT:") :].strip()
                elif current_date:
                    file_path = (repo_root / line).resolve()
                    try:
                        rel = file_path.relative_to(root).as_posix()
                        if rel not in dates:
                            dates[rel] = current_date
                    except ValueError:
                        pass
        else:
            log.warning("git log failed for %s (exit %d): %s", root, res.returncode, res.stderr.strip())
    except Exception as exc:
        log.warning("failed to extract git commit dates for %s: %s", root, exc)
    return dates


def documents_from_docs_dir(
    root: Path, repo_url: str | None = None
) -> Iterator[SourceDocument]:
    root = root.resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"docs directory not found: {root}")
    git_dates = _git_commit_dates_for_dir(root)
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
        metadata: dict[str, Any] = {"path": rel_posix}
        commit_date = git_dates.get(rel_posix)
        if not commit_date:
            try:
                f_res = subprocess.run(
                    ["git", "log", "-1", "--format=%cI", "--", path.name],
                    cwd=path.parent,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if f_res.returncode == 0 and f_res.stdout.strip():
                    commit_date = f_res.stdout.strip()
            except Exception as exc:
                log.warning("failed to extract git commit date for %s: %s", path, exc)
        if commit_date:
            metadata["created_at"] = commit_date

        yield SourceDocument(
            source=DOCS_SOURCE,
            external_id=rel_posix,
            title=_title_from_text(text, path.name),
            url=f"{repo_url.rstrip('/')}/blob/main/{rel_posix}" if repo_url else None,
            text=text,
            metadata=metadata,
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


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._pieces: list[str] = []
        self._block_tags = {
            "p", "h1", "h2", "h3", "h4", "h5", "h6",
            "li", "tr", "div", "blockquote", "pre",
        }

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._block_tags:
            self._pieces.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._block_tags:
            self._pieces.append("\n")
        elif tag in {"td", "th"}:
            self._pieces.append(" ")

    def handle_data(self, data: str) -> None:
        self._pieces.append(data)

    def get_text(self) -> str:
        raw = "".join(self._pieces)
        lines = [line.strip() for line in raw.splitlines()]
        collapsed: list[str] = []
        blank = False
        for line in lines:
            if line:
                collapsed.append(line)
                blank = False
            elif not blank:
                collapsed.append("")
                blank = True
        return html.unescape("\n".join(collapsed)).strip()


def fetch_confluence_pages(
    space_key: str = "RTKB",
    *,
    guard: ExclusionGuard | None = None,
    limit: int = 50,
    search_fn: Any = None,
    get_fn: Any = None,
) -> list[SourceDocument]:
    """Fetch pages from a Confluence space and convert XHTML body to plain text."""
    guard = guard or ExclusionGuard()
    if search_fn is None or get_fn is None:
        from anvil.tools.confluence import confluence_get, confluence_search

        search_fn = search_fn or confluence_search
        get_fn = get_fn or confluence_get

    cql = f"space = {space_key} AND type = page"
    results = search_fn(cql=cql, limit=limit)
    documents: list[SourceDocument] = []
    skipped = 0

    for item in results:
        page_id = item.get("id")
        if not page_id:
            continue
        page_id_str = str(page_id)
        if not page_id_str.isdigit():
            continue

        try:
            page = get_fn(page_id_str)
        except Exception as exc:
            log.warning("failed to fetch confluence page %s: %s", page_id_str, exc)
            continue

        raw_body = page.get("body", "")
        extractor = _HTMLTextExtractor()
        extractor.feed(raw_body)
        body_text = extractor.get_text()

        title = str(
            page.get("title") or item.get("title") or f"Confluence page {page_id_str}"
        ).strip()
        text_parts = [f"# {title}"]
        if body_text:
            text_parts.append(body_text)
        full_text = "\n\n".join(text_parts).strip()

        if not full_text:
            continue

        if guard.is_excluded(CONFLUENCE_SOURCE, page_id_str) or guard.is_content_excluded(
            full_text
        ):
            skipped += 1
            continue

        # Confluence timestamp extraction (ANVIL-68):
        # Prefers true page creation timestamp (createdAt / createdDate).
        # Falling back to versionCreatedAt or lastModified represents a modification timestamp
        # rather than initial page creation, but errs toward hiding post-fix content (conservative
        # leak guard under as_of).
        ver = page.get("version")
        ver_created = ver.get("createdAt") if isinstance(ver, dict) else None
        hist = item.get("history")
        hist_created = hist.get("createdDate") if isinstance(hist, dict) else None

        created_at = (
            page.get("createdAt")
            or page.get("created_at")
            or page.get("versionCreatedAt")
            or ver_created
            or hist_created
            or item.get("lastModified")
            or item.get("created_at")
        )
        metadata: dict[str, Any] = {
            "space": space_key,
            "version": page.get("version"),
        }
        if created_at:
            metadata["created_at"] = str(created_at)

        documents.append(
            SourceDocument(
                source=CONFLUENCE_SOURCE,
                external_id=page_id_str,
                title=title,
                url=page.get("url") or item.get("url"),
                text=full_text,
                metadata=metadata,
            )
        )

    if skipped:
        log.info("excluded %d Confluence page(s) by guard", skipped)
    return documents

