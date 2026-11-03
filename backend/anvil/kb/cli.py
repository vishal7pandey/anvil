"""Command line entry point for seeding and checking the KB.

    uv run python -m anvil.kb init-db
    uv run python -m anvil.kb ingest-docs --clone-to .cache/rtems-docs
    uv run python -m anvil.kb ingest-issues
    uv run python -m anvil.kb search "interrupt latency" --k 5
    uv run python -m anvil.kb stats

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
from .store import KBStore

log = logging.getLogger(__name__)

RTEMS_DOCS_URL = "https://github.com/vishal7pandey/rtems-docs"


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

        if args.command in {"ingest-confluence", "ingest-all"}:
            store.ensure_schema()
            space = getattr(args, "space", "RTKB")
            limit = getattr(args, "limit", 100)
            documents = fetch_confluence_pages(space_key=space, limit=limit)
            report = store.ingest(documents)
            print(json.dumps(_report_dict("confluence", report)))
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

    confluence = sub.add_parser("ingest-confluence", help="ingest pages from Confluence space")
    confluence.add_argument("--space", default="RTKB", help="Confluence space key (default: RTKB)")
    confluence.add_argument("--limit", type=int, default=100, help="maximum pages to fetch")

    sub.add_parser("ingest-all", help="docs + issues + confluence")

    search = sub.add_parser("search", help="hybrid search")
    search.add_argument("query")
    search.add_argument("--k", type=int, default=8)
    search.add_argument(
        "--sources", nargs="*", choices=["rtems-docs", "gitlab-issue", "confluence"]
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


def _report_dict(kind: str, report: object) -> dict[str, object]:
    return {"ok": True, "kind": kind, **asdict(report)}


if __name__ == "__main__":
    sys.exit(main())
