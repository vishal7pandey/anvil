#!/usr/bin/env python3
"""Anvil Demo Reset Automation Script.

Resets all system components back to their clean baseline state prior to
running or rehearsing the demo test cases (TC1, TC2, TC3, TC4).

Usage:
    uv run python backend/scripts/reset_demo.py [--dry-run] [--all]
        [--jira] [--mirrors] [--confluence] [--memories] [--containers]
        [--keep-tc4-run1]
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("reset-demo")

BASELINE_TICKETS = {
    "RTEMS-1": {"status": "To Do", "labels": ["posix", "tc1"]},
    "RTEMS-2": {"status": "To Do", "labels": ["component-upgrade", "tc2"]},
    "RTEMS-3": {"status": "To Do", "labels": ["cra", "tc3"]},
    "RTEMS-4": {"status": "To Do", "labels": ["network", "tc2"]},
    "RTEMS-5": {"status": "To Do", "labels": ["incident-investigation", "tc4"]},
    "RTEMS-6": {"status": "To Do", "labels": ["incident-investigation", "tc4-followup"]},
}

def get_baseline_tickets() -> dict[str, dict[str, Any]]:
    """Return protected baseline tickets: RTEMS-1..6 plus any case tickets defined in cases/*.toml.

    Fails closed: any error loading case files propagates to prevent accidental deletion
    of unlisted case tickets.
    """
    tickets: dict[str, dict[str, Any]] = {
        k: {"status": v["status"], "labels": list(v["labels"])} for k, v in BASELINE_TICKETS.items()
    }
    from anvil.cases import load_cases

    for case in load_cases():
        if case.ticket_key:
            clean_labels = [l for l in case.ticket_labels if l != "anvil"]
            tickets[case.ticket_key] = {"status": "To Do", "labels": clean_labels}
    return tickets

AGENT_SCENARIO_LABELS = {
    "cra",
    "tc3",
    "tc4",
    "tc4-followup",
    "anvil",
    "anvil-in-progress",
    "anvil-done",
    "anvil-needs-human",
}

MIRROR_REPOS = [
    "vishal7pandey/rtems",
    "vishal7pandey/rtems-lwip",
]

PROTECTED_CONFLUENCE_PAGE_IDS = {
    "28541041",  # RTEMS Engineering KB Home
    "28672001",  # ANVIL-13 evidence: Host tools
}


def _is_agent_scenario_issue(labels: list[str]) -> bool:
    for l in labels:
        if l in AGENT_SCENARIO_LABELS or l.startswith("anvil"):
            return True
    return False


def reset_jira(dry_run: bool = False) -> bool:
    """Reset Jira tickets (RTEMS-1..6 and case tickets) to baseline status and labels, and delete unmanaged agent issues."""
    from anvil.tools import jira
    from anvil.tools._atlassian import request

    try:
        baseline_tickets = get_baseline_tickets()
    except Exception as exc:
        logger.error("Failed to load baseline and case tickets (refusing to mutate Jira): %s", exc)
        return False

    keys_sorted = sorted(baseline_tickets.keys(), key=lambda k: int(re.sub(r"\D", "", k) or 0))
    logger.info("--- Resetting Jira Tickets (%s and purging unmanaged agent issues) ---", ", ".join(keys_sorted))
    success = True

    # 1. Reset baseline and case tickets
    for key in keys_sorted:
        spec = baseline_tickets[key]
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

    # 2. Delete agent-created Jira issues (excluding baseline and case tickets)
    try:
        all_rtems: list[dict] = []
        next_token = None
        while True:
            body = {
                "jql": "project = RTEMS ORDER BY key ASC",
                "maxResults": 100,
                "fields": ["summary", "status", "issuetype", "labels", "description", "assignee"],
            }
            if next_token:
                body["nextPageToken"] = next_token
            resp = request("POST", "/rest/api/3/search/jql", json=body)
            batch = resp.get("issues", [])
            all_rtems.extend([jira._summarize(i) for i in batch])
            next_token = resp.get("nextPageToken")
            if not next_token or resp.get("isLast", True) or not batch:
                break

        for i in all_rtems:
            ekey = i.get("key", "")
            match = re.match(r"^RTEMS-(\d+)$", ekey)
            if not match:
                continue

            # Protected tickets (baseline + cases) are never deleted
            if ekey in baseline_tickets:
                continue

            labels = i.get("labels", [])
            if _is_agent_scenario_issue(labels):
                if dry_run:
                    logger.info(
                        "[DRY RUN] Would delete agent-created Jira issue %s (labels: %s): '%s'",
                        ekey,
                        labels,
                        i.get("summary"),
                    )
                else:
                    request("DELETE", f"/rest/api/3/issue/{ekey}")
                    logger.info("Deleted agent-created Jira issue %s (labels: %s)", ekey, labels)
            else:
                logger.info(
                    "Skipping %s without agent-scenario label (labels: %s, summary: '%s')",
                    ekey,
                    labels,
                    i.get("summary"),
                )

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
        logger.info("Processing repository '%s'...", repo)
        try:
            # 1. Close open PRs created by agent
            pulls = _gh("GET", f"/repos/{repo}/pulls", params={"state": "open"})
            for pr in pulls:
                head_ref = pr.get("head", {}).get("ref", "")
                if head_ref.startswith("anvil/"):
                    pr_num = pr["number"]
                    if dry_run:
                        logger.info("[DRY RUN] Would close PR #%d on %s (head: %s)", pr_num, repo, head_ref)
                    else:
                        _gh("PATCH", f"/repos/{repo}/pulls/{pr_num}", json={"state": "closed"})
                        logger.info("Closed PR #%d on %s (head: %s)", pr_num, repo, head_ref)

            # 2. Delete agent remote branches
            branches = _gh("GET", f"/repos/{repo}/branches")
            for b in branches:
                bname = b.get("name", "")
                if bname.startswith("anvil/"):
                    if dry_run:
                        logger.info("[DRY RUN] Would delete branch '%s' on %s", bname, repo)
                    else:
                        _gh("DELETE", f"/repos/{repo}/git/refs/heads/{bname}")
                        logger.info("Deleted branch '%s' on %s", bname, repo)

        except Exception as e:
            logger.error("Failed to reset GitHub mirror %s: %s", repo, e)
            success = False

    return success


def reset_confluence_rtkb(keep_tc4_run1: bool = False, dry_run: bool = False) -> bool:
    """Delete agent-created pages in Confluence space RTKB, purge deleted chunks, and re-ingest."""
    from anvil.tools._atlassian import request
    from anvil.tools.confluence import confluence_get, confluence_search
    import sqlalchemy as sa
    from anvil.kb.config import KBSettings

    logger.info("--- Resetting Confluence Space RTKB (keep_tc4_run1=%s) ---", keep_tc4_run1)
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
                is_tc4 = "rtems-5" in title.lower() or "tc4" in title.lower()
                if not is_tc4:
                    try:
                        p_full = confluence_get(pid)
                        body_text = (p_full.get("body", {}).get("storage", {}).get("value") or "").lower()
                        if "rtems-5" in body_text:
                            is_tc4 = True
                    except Exception:
                        pass
                if is_tc4:
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
            if "RTEMS Engineering KB" in title or "ANVIL-13" in title:
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

        # 2. Clean up pgvector KB chunks (purge chunks of deleted pages)
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


def reset_memories(dry_run: bool = False, keep_tc4_run1: bool = False) -> bool:
    """Clear LangGraph /memories/ lessons in database store table."""
    import sqlalchemy as sa
    from anvil.kb.config import KBSettings

    logger.info("--- Clearing /memories/ Lessons (keep_tc4_run1=%s) ---", keep_tc4_run1)
    success = True

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
                        delete_query = "DELETE FROM store WHERE prefix LIKE 'anvil.memories.%'"
                        if keep_tc4_run1:
                            delete_query += " AND key NOT LIKE '%/investigate/%'"

                        if dry_run:
                            logger.info(
                                "[DRY RUN] Would execute on database %s: %s",
                                db_name,
                                delete_query,
                            )
                        else:
                            res = conn.execute(sa.text(delete_query))
                            logger.info(
                                "Cleared %d memory store entry/entries in database %s (query: %s)",
                                res.rowcount or 0,
                                db_name,
                                delete_query,
                            )
            except Exception as dbe:
                logger.debug("Database %s check skipped or errored: %s", db_name, dbe)

    except Exception as e:
        logger.error("Failed to purge database memory store: %s", e)
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
    parser = argparse.ArgumentParser(description="Anvil Demo Environment Reset Tool")
    parser.add_argument("--all", action="store_true", help="Reset all components (default if no specific component flag is given)")
    parser.add_argument("--jira", action="store_true", help="Reset Jira RTEMS-1..6 and case tickets, delete unmanaged agent issues")
    parser.add_argument("--mirrors", action="store_true", help="Reset GitHub mirrors (close PRs and delete anvil/* branches)")
    parser.add_argument("--confluence", action="store_true", help="Reset Confluence space RTKB (purge agent pages & vector chunks)")
    parser.add_argument("--memories", action="store_true", help="Reset LangGraph /memories/ lessons")
    parser.add_argument("--containers", action="store_true", help="Remove ephemeral sandbox containers")
    parser.add_argument("--keep-tc4-run1", action="store_true", help="Preserve TC4 Run 1 post-mortem and lessons for RTEMS-6 self-learning demo")
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
        if not reset_memories(dry_run=args.dry_run, keep_tc4_run1=args.keep_tc4_run1):
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
