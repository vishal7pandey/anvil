"""Demo reset automation for Anvil live demonstrations.

Resets Jira tickets (RTEMS-1..6), cleans up agent branches and PRs on mirror repos,
purges agent-written pages in Confluence space RTKB while preserving synthetic seeds,
re-indexes the vector knowledge base, and tears down ephemeral sandbox containers.

Usage:
    uv run python scripts/reset_demo.py [--all] [--keep-tc4-run1] [--dry-run]
"""

from __future__ import annotations

import argparse
import logging
import re
import subprocess
import sys
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


def reset_jira(dry_run: bool = False) -> None:
    """Reset Jira tickets RTEMS-1..6 to baseline status and labels."""
    from anvil.tools import jira
    from anvil.tools._atlassian import request

    logger.info("--- Resetting Jira Tickets (RTEMS-1..6) ---")
    for key, spec in BASELINE_TICKETS.items():
        try:
            issue = jira.jira_get(key)
            current_status = issue.get("status")
            current_labels = issue.get("labels", [])
            target_status = spec["status"]
            target_labels = spec["labels"]

            logger.info("Checking %s (Current status: '%s', labels: %s)", key, current_status, current_labels)

            # 1. Transition status if needed
            if current_status != target_status:
                if dry_run:
                    logger.info("[DRY RUN] Would transition %s: '%s' -> '%s'", key, current_status, target_status)
                else:
                    jira.jira_transition(key, target_status)
                    logger.info("Transitioned %s -> '%s'", key, target_status)

            # 2. Reset labels to exact baseline
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


def reset_github_mirrors(dry_run: bool = False) -> None:
    """Close agent PRs and delete agent branches (anvil/*) on mirror repositories."""
    from anvil.tools.github import _gh

    logger.info("--- Resetting GitHub Mirrors ---")
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


def reset_confluence_rtkb(keep_tc4_run1: bool = False, dry_run: bool = False) -> None:
    """Purge agent-written pages in Confluence RTKB and re-ingest to vector DB."""
    from anvil.tools._atlassian import request
    from anvil.tools.confluence import confluence_search

    logger.info("--- Resetting Confluence Space RTKB ---")
    try:
        pages = confluence_search("space = RTKB AND type = page ORDER BY created ASC", limit=100)
        logger.info("Found %d total pages in space RTKB", len(pages))

        pages_to_delete = []
        for p in pages:
            pid = str(p.get("id") or "")
            title = str(p.get("title") or "")

            if not pid or pid == "None":
                continue

            # Skip protected baseline pages
            if pid in PROTECTED_CONFLUENCE_PAGE_IDS:
                continue
            if title.strip().startswith("[synthetic]"):
                continue
            if "RTEMS Engineering KB" in title or "RTKB" in title or "ANVIL-13" in title:
                continue

            # Check if keeping TC4 Run 1 post-mortem for RTEMS-6 follow-up
            if keep_tc4_run1:
                tc4_markers = ["rtems-5", "tm_guidance", "incident analysis", "telemetry packet drops"]
                if any(m in title.lower() for m in tc4_markers):
                    logger.info("Preserving TC4 Run 1 page: %s (id: %s)", title, pid)
                    continue

            pages_to_delete.append((pid, title))

        for pid, title in pages_to_delete:
            if dry_run:
                logger.info("[DRY RUN] Would delete Confluence page '%s' (id: %s)", title, pid)
            else:
                try:
                    request("DELETE", f"/wiki/api/v2/pages/{pid}")
                    logger.info("Deleted Confluence page '%s' (id: %s)", title, pid)
                except Exception as de:
                    logger.error("Failed to delete page %s (%s): %s", title, pid, de)

        # Trigger KB re-ingestion
        if not dry_run:
            logger.info("Re-ingesting Confluence RTKB into vector DB...")
            cmd = [sys.executable, "-m", "anvil.kb.cli", "ingest-confluence", "--space", "RTKB"]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if res.returncode == 0:
                logger.info("RTKB re-ingestion succeeded:\n%s", res.stdout.strip())
            else:
                logger.error("RTKB re-ingestion failed (exit %d):\n%s", res.returncode, res.stderr)

    except Exception as e:
        logger.error("Failed to reset Confluence RTKB: %s", e)


def reset_containers(dry_run: bool = False) -> None:
    """Stop and remove ephemeral Anvil sandbox containers (anvil-sbx-* and anvil-sandbox-*)."""
    logger.info("--- Cleaning Up Sandbox Containers ---")
    try:
        res = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if res.returncode != 0:
            logger.warning("Could not list docker containers: %s", res.stderr)
            return

        names = [line.strip() for line in res.stdout.splitlines() if line.strip()]
        targets = [n for n in names if re.match(r"^anvil-(sbx|sandbox)-", n)]

        if not targets:
            logger.info("No ephemeral Anvil sandbox containers found.")
            return

        logger.info("Found %d container(s) to remove: %s", len(targets), ", ".join(targets))
        for name in targets:
            if dry_run:
                logger.info("[DRY RUN] Would stop and remove container '%s'", name)
            else:
                subprocess.run(["docker", "rm", "-f", name], capture_output=True, text=True, timeout=30)
                logger.info("Removed container '%s'", name)

    except Exception as e:
        logger.error("Failed to clean up containers: %s", e)


def main() -> None:
    parser = argparse.ArgumentParser(description="Anvil Demo Environment Reset")
    parser.add_argument("--jira", action="store_true", help="Reset Jira tickets RTEMS-1..6")
    parser.add_argument("--mirrors", action="store_true", help="Clean up mirror branches and PRs")
    parser.add_argument("--confluence", action="store_true", help="Purge agent RTKB pages and re-ingest")
    parser.add_argument("--containers", action="store_true", help="Remove ephemeral sandbox containers")
    parser.add_argument("--all", action="store_true", help="Execute all reset operations (default)")
    parser.add_argument("--keep-tc4-run1", action="store_true", help="Keep TC4 Run 1 post-mortem for RTEMS-6 run")
    parser.add_argument("--dry-run", action="store_true", help="Simulate actions without mutating state")

    args = parser.parse_args()

    run_all = args.all or not (args.jira or args.mirrors or args.confluence or args.containers)

    logger.info("Starting Anvil demo reset (dry_run=%s, keep_tc4_run1=%s)...", args.dry_run, args.keep_tc4_run1)

    if run_all or args.jira:
        reset_jira(dry_run=args.dry_run)

    if run_all or args.mirrors:
        reset_github_mirrors(dry_run=args.dry_run)

    if run_all or args.confluence:
        reset_confluence_rtkb(keep_tc4_run1=args.keep_tc4_run1, dry_run=args.dry_run)

    if run_all or args.containers:
        reset_containers(dry_run=args.dry_run)

    logger.info("Anvil demo reset completed successfully.")


if __name__ == "__main__":
    main()

