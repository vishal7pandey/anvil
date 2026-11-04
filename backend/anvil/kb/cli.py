"""Command line entry point for seeding and checking the KB.

    uv run python -m anvil.kb init-db
    uv run python -m anvil.kb ingest-docs --clone-to .cache/rtems-docs
    uv run python -m anvil.kb ingest-issues
    uv run python -m anvil.kb search "interrupt latency" --k 5
    uv run python -m anvil.kb stats
    uv run python -m anvil.kb import-snapshot      # load data/kb (no network except the embedder)
    uv run python -m anvil.kb export-snapshot      # refresh data/kb from the live KB

The GitLab and clone steps use the network. On the shared laptop take
``C:\\Dev\\comms\\locks\\network-heavy.lock`` before a large clone.
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from .config import KBSettings
from .sources import (
    documents_from_docs_dir,
    fetch_closed_gitlab_issues,
    fetch_confluence_pages,
)
from .gitlab import GitLabClient, fetch_gitlab_items
from .snapshot import export_snapshot, import_snapshot
from .store import KBStore

log = logging.getLogger(__name__)

RTEMS_DOCS_URL = "https://github.com/vishal7pandey/rtems-docs"
# <repo>/data/kb: the KB snapshot committed for offline handoff.
DEFAULT_SNAPSHOT_DIR = Path(__file__).resolve().parents[3] / "data" / "kb"


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    settings = KBSettings()

    with KBStore(settings) as store:
        if args.command == "init-db":
            store.ensure_schema()
            print(json.dumps({"ok": True, "action": "init-db"}))
            return 0

        if args.command == "stats":
            print(json.dumps(store.stats()))
            return 0

        if args.command in {"ingest-docs", "ingest-all"}:
            store.ensure_schema()
            repo_dir = _resolve_docs_dir(args, settings)
            documents = list(documents_from_docs_dir(repo_dir, args.repo_url))
            report = store.ingest(documents)
            print(json.dumps(_report_dict("docs", report)))
            if args.command == "ingest-docs":
                return 0

        if args.command in {"ingest-issues", "ingest-all"}:
            store.ensure_schema()
            documents = fetch_closed_gitlab_issues(settings, max_pages=args.max_pages)
            report = store.ingest(documents)
            print(json.dumps(_report_dict("issues", report)))
            if args.command == "ingest-issues":
                return 0

        if args.command == "ingest-gitlab":
            store.ensure_schema()
            print(json.dumps(_ingest_gitlab(args, settings, store)))
            return 0

        if args.command == "backfill-created-at":
            store.ensure_schema()
            print(json.dumps(_backfill_created_at(store)))
            return 0

        if args.command in {"ingest-confluence", "ingest-all"}:
            store.ensure_schema()
            space = getattr(args, "space", "RTKB")
            limit = getattr(args, "limit", 100)
            documents = fetch_confluence_pages(space_key=space, limit=limit)
            report = store.ingest(documents)
            print(json.dumps(_report_dict("confluence", report)))
            return 0

        if args.command == "ingest-local":
            store.ensure_schema()
            from anvil.kb.local import LocalKnowledge

            documents = LocalKnowledge(args.dir).documents()
            report = store.ingest(documents)
            print(json.dumps(_report_dict("local", report)))
            return 0

        if args.command == "export-snapshot":
            print(json.dumps(export_snapshot(store, args.out, with_vectors=args.with_vectors), indent=2))
            return 0

        if args.command == "import-snapshot":
            print(json.dumps(asdict(import_snapshot(store, args.src))))
            return 0

        if args.command == "search":
            hits = store.search(args.query, k=args.k, sources=args.sources or None)
            print(
                json.dumps(
                    [
                        {
                            "title": hit.title,
                            "source": hit.source,
                            "external_id": hit.external_id,
                            "url": hit.url,
                            "score": round(hit.score, 6),
                            "content": hit.content,
                        }
                        for hit in hits
                    ],
                    indent=2,
                )
            )
            return 0

    parser.print_help()
    return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="anvil.kb", description="Anvil knowledge base"
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="create the kb tables and indexes")
    sub.add_parser("stats", help="count documents and chunks")

    docs = sub.add_parser("ingest-docs", help="ingest a local rtems-docs checkout")
    docs.add_argument("--repo-dir", type=Path)
    docs.add_argument(
        "--clone-to", type=Path, help="clone the rtems-docs mirror here if missing"
    )
    docs.add_argument("--repo-url", default=RTEMS_DOCS_URL)

    issues = sub.add_parser("ingest-issues", help="ingest closed RTEMS GitLab issues")
    issues.add_argument("--max-pages", type=int, default=None)

    gitlab = sub.add_parser(
        "ingest-gitlab",
        help="issues and merge requests with their comments, all states; resumable",
    )
    gitlab.add_argument("--projects", nargs="*", help="default: KB_GITLAB_PROJECTS")
    gitlab.add_argument("--kinds", nargs="*", default=["issues", "merge_requests"],
                        choices=["issues", "merge_requests"])
    gitlab.add_argument("--since", help="updated_after (ISO date); default: the saved cursor")
    gitlab.add_argument("--max-pages", type=int, default=None)
    gitlab.add_argument("--no-notes", action="store_true", help="skip comments (fast, 1 request per 100 items)")

    sub.add_parser(
        "backfill-created-at",
        help="fill kb_documents.created_at from doc_metadata for gitlab sources (idempotent, ANVIL-43)",
    )

    confluence = sub.add_parser("ingest-confluence", help="ingest pages from Confluence space")
    confluence.add_argument("--space", default="RTKB", help="Confluence space key (default: RTKB)")
    confluence.add_argument("--limit", type=int, default=100, help="maximum pages to fetch")

    from anvil.kb.local import knowledge_dir

    loc = sub.add_parser("ingest-local", help="ingest the local knowledge folder (ANVIL-60)")
    loc.add_argument("--dir", type=Path, default=knowledge_dir(),
                     help="folder of Markdown pages (default: ANVIL_KNOWLEDGE_DIR or kb/local)")

    sub.add_parser("ingest-all", help="docs + issues + confluence")

    exp = sub.add_parser("export-snapshot", help="write documents + chunks (no vectors) to files")
    exp.add_argument("--out", type=Path, default=DEFAULT_SNAPSHOT_DIR)
    exp.add_argument("--with-vectors", action="store_true",
                     help="also write the pgvector text per chunk (ANVIL-58; large file, shared out-of-band)")
    imp = sub.add_parser("import-snapshot", help="load a snapshot and re-embed it with the configured model")
    imp.add_argument("--from", dest="src", type=Path, default=DEFAULT_SNAPSHOT_DIR)

    search = sub.add_parser("search", help="hybrid search")
    search.add_argument("query")
    search.add_argument("--k", type=int, default=8)
    search.add_argument(
        "--sources", nargs="*",
        choices=["rtems-docs", "gitlab-issue", "gitlab-mr", "mailing-list", "confluence", "local"],
    )
    return parser


def _resolve_docs_dir(args: argparse.Namespace, settings: KBSettings) -> Path:
    if getattr(args, "repo_dir", None):
        return args.repo_dir
    clone_to = getattr(args, "clone_to", None)
    if not clone_to:
        raise SystemExit("pass --repo-dir PATH or --clone-to PATH")
    if not (clone_to / ".git").exists():
        clone_to.parent.mkdir(parents=True, exist_ok=True)
        log.info("cloning %s into %s", RTEMS_DOCS_URL, clone_to)
        subprocess.run(
            ["git", "clone", "--depth", "1", RTEMS_DOCS_URL, str(clone_to)],
            check=True,
        )
    return clone_to


def _ingest_gitlab(args: argparse.Namespace, settings: KBSettings, store: KBStore) -> dict[str, object]:
    """Crawl each project/kind from its saved cursor, ingesting page by page."""
    state_path = Path(settings.crawl_state_path)
    state: dict[str, str] = json.loads(state_path.read_text()) if state_path.exists() else {}

    def save(key: str, cursor: str) -> None:
        state[key] = cursor
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, indent=2, sort_keys=True))

    gl = GitLabClient(settings.gitlab_url, settings.gitlab_token)
    results: dict[str, object] = {}
    try:
        for project in args.projects or settings.gitlab_projects:
            for kind in args.kinds:
                key = f"{settings.gitlab_url}|{project}|{kind}"
                documents = fetch_gitlab_items(
                    gl, project, kind,
                    updated_after=args.since or state.get(key),
                    with_notes=not args.no_notes,
                    max_pages=args.max_pages,
                    on_page=lambda cursor, key=key: save(key, cursor),
                )
                report = store.ingest(documents)
                results[f"{project}:{kind}"] = asdict(report)
                log.info("%s %s: %s", project, kind, report)
    finally:
        gl.close()
    return {"ok": True, "kind": "gitlab", "results": results}


def _backfill_created_at(store: KBStore) -> dict[str, object]:
    """Set ``created_at`` from ``doc_metadata`` for GitLab issues/MRs (ANVIL-43).

    Idempotent: only touches rows where ``created_at IS NULL``. Docs/Confluence have no upstream
    creation date and stay NULL = always visible.
    """
    from sqlalchemy import text

    with store.engine.begin() as conn:
        result = conn.execute(
            text(
                "UPDATE kb_documents "
                "SET created_at = (doc_metadata->>'created_at')::timestamptz "
                "WHERE created_at IS NULL "
                "AND source IN ('gitlab-issue', 'gitlab-mr') "
                "AND doc_metadata ? 'created_at' "
                "AND doc_metadata->>'created_at' IS NOT NULL"
            )
        )
        updated = result.rowcount
    log.info("backfill-created-at: %s documents", updated)
    return {"ok": True, "documents_updated": updated}


def _report_dict(kind: str, report: object) -> dict[str, object]:
    return {"ok": True, "kind": kind, **asdict(report)}


if __name__ == "__main__":
    sys.exit(main())
