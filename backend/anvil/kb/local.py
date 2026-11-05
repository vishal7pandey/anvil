"""A local knowledge folder: Confluence-shaped pages on disk + immediate KB write-back (ANVIL-60/59).

When a laptop has no Atlassian (or the run is local:), the agent's `confluence_*` and
`publish_evidence` tools are bound to this store instead of the REST tools — the tool NAMES are
kept so skills and the approval gate are unchanged. Every page written here is also ingested into
the KB at once, so a later run (or a later `kb_search` in the same run) finds it: TC4's "run 2
learns from run 1" without a reset step.

Folder: ANVIL_KNOWLEDGE_DIR, default <repo>/kb/local/. Each page is Markdown with a small front
matter block (title, space, created/updated, version); the page slug is the file name, the space
the folder. `python -m anvil.kb ingest-local --dir ...` reads an existing folder the same way
(seed: docs/confluence/RTKB/*.md).
"""

from __future__ import annotations

import logging
import os
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from anvil.kb.sources import SourceDocument

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
LOCAL_SOURCE = "local"
_PAGE_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)


def knowledge_dir() -> Path:
    return Path(os.environ.get("ANVIL_KNOWLEDGE_DIR") or REPO_ROOT / "kb" / "local")


def slugify(title: str) -> str:
    slug = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", slug).strip("-.") or "page"
    return slug[:80]


def _front_matter(title: str, space: str, created: str, updated: str, version: int) -> str:
    return (f"---\ntitle: {title}\nspace: {space}\ncreated: {created}\nupdated: {updated}\n"
            f"version: {version}\n---\n")


def _meta(text: str) -> dict[str, str]:
    m = _PAGE_RE.match(text)
    if not m:
        return {}
    out: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out


class LocalKnowledge:
    """Confluence-shaped pages under ANVIL_KNOWLEDGE_DIR (default kb/local/)."""

    def __init__(self, root: str | Path | None = None, *, store: Any = None):
        self.root = Path(root) if root else knowledge_dir()
        self._store = store  # injectable; None = get_store() lazily

    # -- store access (lazy so importing this module never needs a live KB) ----------------------

    def _index(self, doc: SourceDocument) -> bool:
        """Ingest one page into the KB (leak guard inside store.ingest). Best-effort: the page write
        must not fail because the KB is down — callers get kb_indexed=False instead."""
        try:
            store = self._store
            if store is None:
                from anvil.kb.tool import get_store

                store = get_store()
            report = store.ingest([doc])
            return (report.documents_added + report.documents_updated) > 0
        except Exception as exc:  # noqa: BLE001 - KB may be unavailable; the page is still on disk
            log.warning("KB write-back for %s failed: %s", doc.external_id, exc)
            return False

    # -- Confluence-shaped API (same names/return shapes as tools/confluence.py) -----------------

    _SPACE_RE = re.compile(r"^[A-Za-z0-9_-]+$")

    def write_page(self, space_key: str, title: str, markdown: str,
                   parent_id: str | None = None) -> dict[str, Any]:
        """Upsert <root>/<SPACE>/<slug>.md (same title -> same slug) and index it at once."""
        from anvil.kb.config import empty_guard_writes_allowed
        from anvil.kb.exclusions import ExclusionGuard
        from anvil.tools.errors import ToolError

        if ExclusionGuard().is_empty and not empty_guard_writes_allowed():
            # ANVIL-100 #24 round 2: write-back also feeds the KB — same refusal as ingest.
            raise ToolError(
                "KB leak guard is empty (no eval cases loaded): knowledge writes are refused so "
                "withheld answers cannot enter the KB silently. If this deployment runs no eval "
                "cases, set ANVIL_KB_ALLOW_EMPTY_GUARD=1."
            )
        space = space_key.upper()
        if not self._SPACE_RE.fullmatch(space):  # space_key is agent input: never a path
            raise ToolError(f"refused: knowledge space '{space_key}' is not a folder-safe name")
        slug = slugify(title)
        path = self.root / f"{space}/{slug}.md"
        if path.exists():
            old = _meta(path.read_text(encoding="utf-8"))
            if old.get("title") and old["title"] != title:  # different page, same slug
                slug = f"{slug}-{abs(hash(title)) % 0xFFFF:04x}"
                path = self.root / f"{space}/{slug}.md"
                old = {}
        else:
            old = {}
        rel = f"{space}/{slug}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        version = int(old.get("version") or 0) + 1
        created = old.get("created") or now
        body = _front_matter(title, space, created, now, version) + "\n" + markdown.strip() + "\n"
        path.write_text(body, encoding="utf-8")
        indexed = self._index(SourceDocument(
            source=LOCAL_SOURCE, external_id=rel, title=title, url=None,
            text=markdown.strip(),
            # created_at feeds kb_documents.created_at: without it the page is undated and visible
            # under a case's as_of guard — run 2 would read run 1's evidence (review on #95).
            metadata={"path": rel, "space": space, "updated": now, "created_at": now},
        ))
        return {"id": rel, "title": title, "version": version, "url": str(path),
                "kb_indexed": indexed}

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Local stand-in for confluence_search: case-insensitive substring match over page text.
        CQL won't parse here; agents search the folder by words, or use kb_search for ranked hits."""
        needle = re.sub(r'[\'"~]', "", query).strip().lower()
        out: list[dict[str, Any]] = []
        for path in sorted(self.root.rglob("*.md")):
            if len(out) >= limit:
                break
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            meta = _meta(text)
            hay = (meta.get("title", "") + "\n" + text).lower()
            if needle and needle in hay:
                rel = path.relative_to(self.root).as_posix()
                out.append({"id": rel, "title": meta.get("title") or path.stem,
                            "space": meta.get("space") or path.parent.name,
                            "excerpt": _excerpt(text, needle),
                            "url": str(path)})
        return out

    def get(self, page_id: str) -> dict[str, Any]:
        """Local stand-in for confluence_get: read a page by its folder-relative id."""
        rel = page_id.strip().lstrip("/")
        path = (self.root / rel).resolve()
        # is_relative_to, not a string prefix: 'kb-local-private' shares 'kb-local' as text (review on #95).
        if not path.is_relative_to(self.root.resolve()) or path.suffix.lower() != ".md" \
                or not path.is_file():
            from anvil.tools.errors import ToolError

            raise ToolError(f"no local knowledge page '{page_id}'")
        text = path.read_text(encoding="utf-8", errors="replace")
        meta = _meta(text)
        return {"id": rel, "title": meta.get("title") or path.stem,
                "version": int(meta.get("version") or 1),
                "body": _PAGE_RE.sub("", text, count=1).strip(), "url": str(path)}

    # -- ingest source (python -m anvil.kb ingest-local) ----------------------------------------

    def documents(self) -> Iterator[SourceDocument]:
        """Every .md page as a SourceDocument(source='local'); date from front matter else mtime."""
        for path in sorted(self.root.rglob("*.md")):
            if any(part.startswith(".") for part in path.relative_to(self.root).parts):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if not text.strip():
                continue
            meta = _meta(text)
            rel = path.relative_to(self.root).as_posix()
            when = meta.get("date") or meta.get("created") or _mtime(path)
            yield SourceDocument(
                source=LOCAL_SOURCE, external_id=rel,
                title=meta.get("title") or path.stem, url=None,
                text=_PAGE_RE.sub("", text, count=1).strip(),
                metadata={"path": rel, "space": meta.get("space") or path.parent.name,
                          "date": when, "created_at": when},
            )


def _mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _excerpt(text: str, needle: str, width: int = 160) -> str:
    i = text.lower().find(needle.lower())
    if i < 0:
        return text[:width].strip()
    lo = max(0, i - width // 2)
    return text[lo:lo + width].strip()
