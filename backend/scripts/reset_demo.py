"""Demo reset automation for Anvil live demonstrations.

Resets Jira tickets (RTEMS-1..6 and deletes RTEMS-7+), cleans up agent branches and PRs on mirror repos,
purges agent-written pages in Confluence space RTKB and clears orphaned chunks in pgvector,
clears LangGraph /memories/ lessons in database and disk, re-indexes the vector knowledge base,
and tears down ephemeral sandbox containers.

Usage:
    uv run python scripts/reset_demo.py [--all] [--keep-tc4-run1] [--dry-run]
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("reset_demo")

BASELINE_TICKETS: dict[str, dict[str, Any]] = {
    "RTEMS-1": {"status": "To Do", "labels": ["posix", "tc1"]},
    "RTEMS-2": {"status": "To Do", "labels": ["component-upgrade", "tc2"]},
    "RTEMS-3": {"status": "To Do", "labels": ["cra", "tc3"]},
    "RTEMS-4": {"status": "To Do", "labels": ["network", "tc2"]},
    "RTEMS-5": {"status": "To Do", "labels": ["incident-investigation", "tc4"]},
    "RTEMS-6": {"status": "To Do", "labels": ["incident-investigation", "tc4-followup"]},
}

MIRROR_REPOS = [
    "vishal7pandey/rtems",
    "vishal7pandey/rtems-lwip",
]

PROTECTED_CONFLUENCE_PAGE_IDS = {
    "28541041",  # RTEMS Engineering KB Home
    "28672001",  # ANVIL-13 evidence: Host tools
}


def reset_jira(dry_run: bool = False) -> bool:
    """Reset Jira tickets RTEMS-1..6 to baseline status and labels, and delete RTEMS-7+ issues."""
    from anvil.tools import jira
    from anvil.tools._atlassian import request

    logger.info("--- Resetting Jira Tickets (RTEMS-1..6 and purging RTEMS-7+) ---")
    success = True

    # 1. Reset baseline tickets
    for key, spec in BASELINE_TICKETS.items():
        try:
            issue = jira.jira_get(key)
            current_status = issue.get("status")
            current_labels = issue.get("labels", [])
            target_status = spec["status"]
            target_labels = spec["labels"]

            logger.info("Checking %s (Current status: '%s', labels: %s)", key, current_status, current_labels)

            # Transition status if needed
            if current_status != target_status:
                if dry_run:
                    logger.info("[DRY RUN] Would transition %s: '%s' -> '%s'", key, current_status, target_status)
                else:
                    jira.jira_transition(key, target_status)
                    logger.info("Transitioned %s -> '%s'", key, target_status)

            # Reset labels to exact baseline
            if set(current_labels) != set(target_labels):
                if dry_run:
                    logger.info("[DRY RUN] Would reset labels on %s: %s -> %s", key, current_labels, target_labels)
                else:
                    request("PUT", f"/rest/api/3/issue/{key}", json={"fields": {"labels": target_labels, "assignee": None}})
                    logger.info("Reset labels on %s -> %s", key, target_labels)
            else:
                logger.info("%s labels already match baseline.", key)

        except Exception as e:
            logger.error("Failed to reset Jira ticket %s: %s", key, e)
            success = False

    # 2. Delete agent-created Jira issues (RTEMS-7+)
    try:
        baseline_keys = set(BASELINE_TICKETS.keys())
        all_rtems = jira.jira_search("project = RTEMS ORDER BY key ASC", max_results=100)
        extra_issues = [i for i in all_rtems if i["key"] not in baseline_keys]

        if extra_issues:
            logger.info("Found %d agent-created Jira issue(s) to delete: %s", len(extra_issues), [i["key"] for i in extra_issues])
            for i in extra_issues:
                ekey = i["key"]
                if dry_run:
                    logger.info("[DRY RUN] Would delete agent-created Jira issue %s: '%s'", ekey, i.get("summary"))
                else:
                    request("DELETE", f"/rest/api/3/issue/{ekey}")
                    logger.info("Deleted agent-created Jira issue %s", ekey)
        else:
            logger.info("No extra agent-created RTEMS issues found.")

    except Exception as e:
        logger.error("Failed to query or delete extra Jira issues: %s", e)
        success = False

    return success


def reset_github_mirrors(dry_run: bool = False) -> bool:
    """Close agent PRs and delete agent branches (anvil/*) on mirror repositories."""
    from anvil.tools.github import _gh

    logger.info("--- Resetting GitHub Mirrors ---")
    success = True

    for repo in MIRROR_REPOS:
        logger.info("Processing repository %s...", repo)
        try:
            # 1. Close open PRs originating from anvil/* branches
            prs = _gh("GET", f"/repos/{repo}/pulls", params={"state": "open", "per_page": 50})
            for pr in prs:
                head_ref = pr["head"]["ref"]
                pr_num = pr["number"]
                if head_ref.startswith("anvil/"):
                    if dry_run:
                        logger.info("[DRY RUN] Would close PR #%d (%s) on %s", pr_num, head_ref, repo)
                    else:
                        _gh("PATCH", f"/repos/{repo}/pulls/{pr_num}", json={"state": "closed"})
                        logger.info("Closed PR #%d (%s) on %s", pr_num, head_ref, repo)

            # 2. Delete remote branches matching anvil/*
            branches = _gh("GET", f"/repos/{repo}/branches", params={"per_page": 100})
            for b in branches:
                bname = b["name"]
                if bname.startswith("anvil/"):
                    if dry_run:
                        logger.info("[DRY RUN] Would delete branch %s on %s", bname, repo)
                    else:
                        _gh("DELETE", f"/repos/{repo}/git/refs/heads/{bname}")
                        logger.info("Deleted branch %s on %s", bname, repo)

        except Exception as e:
            logger.error("Error processing repo %s: %s", repo, e)
            success = False

    return success


def reset_confluence_rtkb(keep_tc4_run1: bool = False, dry_run: bool = False) -> bool:
    """Purge agent-written pages in Confluence RTKB and clear corresponding pgvector chunks."""
    from anvil.kb.config import KBSettings
    from anvil.tools._atlassian import request
    from anvil.tools.confluence import confluence_search
    import sqlalchemy as sa

    logger.info("--- Resetting Confluence Space RTKB & pgvector Chunks ---")
    success = True

    try:
        pages = confluence_search("space = RTKB AND type = page ORDER BY created ASC", limit=100)
        logger.info("Found %d total pages in space RTKB", len(pages))

        pages_to_delete = []
        valid_page_ids = []

        for p in pages:
            pid = str(p.get("id") or "")
            title = str(p.get("title") or "")

            if not pid or pid == "None":
                continue

            # Check if keeping TC4 Run 1 post-mortem for RTEMS-6 follow-up
            if keep_tc4_run1:
                tc4_markers = ["rtems-5", "tm_guidance", "incident analysis", "telemetry packet drops"]
                if any(m in title.lower() for m in tc4_markers):
                    logger.info("Preserving TC4 Run 1 page: %s (id: %s)", title, pid)
                    valid_page_ids.append(pid)
                    continue

            # Skip protected baseline pages
            if pid in PROTECTED_CONFLUENCE_PAGE_IDS:
                valid_page_ids.append(pid)
                continue
            if title.strip().startswith("[synthetic]"):
                valid_page_ids.append(pid)
                continue
            if "RTEMS Engineering KB" in title or "RTKB" in title or "ANVIL-13" in title:
                valid_page_ids.append(pid)
                continue

            pages_to_delete.append((pid, title))

        # 1. Delete pages from Confluence
        for pid, title in pages_to_delete:
            if dry_run:
                logger.info("[DRY RUN] Would delete Confluence page '%s' (id: %s)", title, pid)
            else:
                try:
                    request("DELETE", f"/wiki/api/v2/pages/{pid}")
                    logger.info("Deleted Confluence page '%s' (id: %s)", title, pid)
                except Exception as de:
                    logger.error("Failed to delete page %s (%s): %s", title, pid, de)
                    success = False

        # 2. Clean up pgvector KB chunks (Item 1: purge chunks of deleted pages)
        try:
            db_url = KBSettings().database_url
            engine = sa.create_engine(db_url)
            with engine.begin() as conn:
                if pages_to_delete:
                    del_pids = [pid for pid, _ in pages_to_delete]
                    if dry_run:
                        logger.info("[DRY RUN] Would purge pgvector documents & chunks for deleted pages: %s", del_pids)
                    else:
                        res = conn.execute(
                            sa.text("DELETE FROM kb_documents WHERE source = 'confluence' AND external_id = ANY(:pids)"),
                            {"pids": del_pids},
                        )
                        logger.info("Purged %d deleted Confluence document(s) and cascading chunks from pgvector.", res.rowcount)

                # Reconcile any orphan confluence chunks in pgvector not in valid pages
                if not dry_run and valid_page_ids:
                    res = conn.execute(
                        sa.text("DELETE FROM kb_documents WHERE source = 'confluence' AND NOT (external_id = ANY(:valid_pids))"),
                        {"valid_pids": valid_page_ids},
                    )
                    if res.rowcount > 0:
                        logger.info("Purged %d orphan Confluence document(s) from pgvector.", res.rowcount)
        except Exception as pe:
            logger.error("Failed to clean up pgvector chunks: %s", pe)
            success = False

        # 3. Trigger KB re-ingestion
        if not dry_run:
            logger.info("Re-ingesting Confluence RTKB into vector DB...")
            cmd = [sys.executable, "-m", "anvil.kb.cli", "ingest-confluence", "--space", "RTKB"]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if res.returncode == 0:
                logger.info("RTKB re-ingestion succeeded.")
            else:
                logger.error("RTKB re-ingestion failed (exit %d):\n%s", res.returncode, res.stderr)
                success = False

    except Exception as e:
        logger.error("Failed to reset Confluence RTKB: %s", e)
        success = False

    return success


def reset_memories(dry_run: bool = False) -> bool:
    """Clear LangGraph /memories/ lessons in database and disk (Item 2)."""
    import sqlalchemy as sa
    from anvil.kb.config import KBSettings

    logger.info("--- Clearing /memories/ Lessons ---")
    success = True

    # 1. Clean database store tables (LangGraph AsyncPostgresStore)
    try:
        base_db_url = KBSettings().database_url
        admin_engine = sa.create_engine(base_db_url)
        with admin_engine.connect() as conn:
            db_names = [
                r[0]
                for r in conn.execute(
                    sa.text("SELECT datname FROM pg_database WHERE datname LIKE :p"),
                    {"p": "anvil%"},
                ).fetchall()
            ]

        for db_name in db_names:
            db_url = re.sub(r"/[^/]+$", f"/{db_name}", base_db_url)
            try:
                db_engine = sa.create_engine(db_url)
                with db_engine.begin() as conn:
                    check = conn.execute(
                        sa.text("SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'store')")
                    ).scalar()
                    if check:
                        if dry_run:
                            logger.info("[DRY RUN] Would purge memory lessons from store table in database %s", db_name)
                        else:
                            res = conn.execute(sa.text("DELETE FROM store"))
                            logger.info("Cleared %d memory store entry/entries in database %s", res.rowcount or 0, db_name)
            except Exception as dbe:
                logger.debug("Database %s check skipped or errored: %s", db_name, dbe)

    except Exception as e:
        logger.error("Failed to purge database memory store: %s", e)
        success = False

    # 2. Clean disk-based memory directories if present
    repo_root = Path(__file__).resolve().parents[2]
    candidate_paths = [
        repo_root / "memories",
        Path("C:/Dev/memories"),
        repo_root / "backend" / "memories",
    ]
    for mpath in candidate_paths:
        if mpath.exists():
            if dry_run:
                logger.info("[DRY RUN] Would remove memory directory: %s", mpath)
            else:
                try:
                    shutil.rmtree(mpath)
                    logger.info("Removed memory directory: %s", mpath)
                except Exception as de:
                    logger.error("Failed to remove memory directory %s: %s", mpath, de)
                    success = False

    return success


def reset_containers(dry_run: bool = False) -> bool:
    """Stop and remove ephemeral Anvil sandbox containers (anvil-sbx-* and anvil-sandbox-*)."""
    logger.info("--- Cleaning Up Sandbox Containers ---")
    success = True

    try:
        res = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if res.returncode != 0:
            logger.warning("Could not list docker containers: %s", res.stderr)
            return False

        names = [line.strip() for line in res.stdout.splitlines() if line.strip()]
        targets = [n for n in names if re.match(r"^anvil-(sbx|sandbox)-", n)]

        if not targets:
            logger.info("No ephemeral Anvil sandbox containers found.")
            return True

        logger.info("Found %d container(s) to remove: %s", len(targets), ", ".join(targets))
        for name in targets:
            if dry_run:
                logger.info("[DRY RUN] Would stop and remove container '%s'", name)
            else:
                crm = subprocess.run(["docker", "rm", "-f", name], capture_output=True, text=True, timeout=30)
                if crm.returncode == 0:
                    logger.info("Removed container '%s'", name)
                else:
                    logger.error("Failed to remove container '%s': %s", name, crm.stderr)
                    success = False

    except Exception as e:
        logger.error("Failed to clean up containers: %s", e)
        success = False

    return success


def main() -> None:
    parser = argparse.ArgumentParser(description="Anvil Demo Environment Reset")
    parser.add_argument("--jira", action="store_true", help="Reset Jira tickets RTEMS-1..6 and delete RTEMS-7+")
    parser.add_argument("--mirrors", action="store_true", help="Clean up mirror branches and PRs")
    parser.add_argument("--confluence", action="store_true", help="Purge agent RTKB pages and clear pgvector chunks")
    parser.add_argument("--memories", action="store_true", help="Clear LangGraph /memories/ lessons")
    parser.add_argument("--containers", action="store_true", help="Remove ephemeral sandbox containers")
    parser.add_argument("--all", action="store_true", help="Execute all reset operations (default)")
    parser.add_argument("--keep-tc4-run1", action="store_true", help="Keep TC4 Run 1 post-mortem for RTEMS-6 run")
    parser.add_argument("--dry-run", action="store_true", help="Simulate actions without mutating state")

    args = parser.parse_args()

    run_all = args.all or not (args.jira or args.mirrors or args.confluence or args.memories or args.containers)

    logger.info("Starting Anvil demo reset (dry_run=%s, keep_tc4_run1=%s)...", args.dry_run, args.keep_tc4_run1)

    failures: list[str] = []

    if run_all or args.jira:
        if not reset_jira(dry_run=args.dry_run):
            failures.append("jira")

    if run_all or args.mirrors:
        if not reset_github_mirrors(dry_run=args.dry_run):
            failures.append("mirrors")

    if run_all or args.confluence:
        if not reset_confluence_rtkb(keep_tc4_run1=args.keep_tc4_run1, dry_run=args.dry_run):
            failures.append("confluence")

    if run_all or args.memories:
        if not reset_memories(dry_run=args.dry_run):
            failures.append("memories")

    if run_all or args.containers:
        if not reset_containers(dry_run=args.dry_run):
            failures.append("containers")

    if failures:
        logger.error("Anvil demo reset completed with errors in: %s", ", ".join(failures))
        sys.exit(1)

    logger.info("Anvil demo reset completed successfully.")


if __name__ == "__main__":
    main()
