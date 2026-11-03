"""Host-side TC1 hunk-overlap scorer (ANVIL-22).

Fetches the upstream fix diff (`a62e60b5` on `vishal7pandey/rtems`) on the host only
(never inside a sandbox) and checks, for each upstream hunk in `cpukit/posix/src/mutexinit.c`,
whether the agent's diff modifies the same base-file lines within ±3 lines.

Usage:
    uv run --no-sync python scripts/score_tc1.py <agent.diff>
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import httpx

from anvil.tools.github import GitHubSettings

UPSTREAM_REPO = "vishal7pandey/rtems"
UPSTREAM_COMMIT = "a62e60b5"
TARGET_FILE = "cpukit/posix/src/mutexinit.c"
UPSTREAM_FILES = {"cpukit/posix/src/mutexinit.c", "testsuites/psxtests/psx05/init.c"}
LINE_TOLERANCE = 3

_DIFF_FILE_RE = re.compile(r"^diff --git a/(\S+) b/(\S+)")
_HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")


def fetch_upstream_diff(repo: str = UPSTREAM_REPO, commit: str = UPSTREAM_COMMIT) -> str:
    """Fetch the upstream commit unified diff from GitHub on the host."""
    settings = GitHubSettings()
    headers = {
        "Accept": "application/vnd.github.diff",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"
    url = f"{settings.github_api.rstrip('/')}/repos/{repo}/commits/{commit}"
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        resp = client.get(url, headers=headers)
        resp.raise_for_status()
        return resp.text


def parse_unified_diff(diff_text: str) -> dict[str, list[dict[str, Any]]]:
    """Parse a unified diff into per-file hunks with changed base-file line numbers."""
    files: dict[str, list[dict[str, Any]]] = {}
    current_file: str | None = None
    current_hunk: dict[str, Any] | None = None
    old_line = 0

    for raw_line in diff_text.splitlines():
        file_match = _DIFF_FILE_RE.match(raw_line)
        if file_match:
            current_file = file_match.group(2)
            files.setdefault(current_file, [])
            current_hunk = None
            continue

        if current_file is None:
            continue

        hunk_match = _HUNK_HEADER_RE.match(raw_line)
        if hunk_match:
            old_start = int(hunk_match.group(1))
            old_count = int(hunk_match.group(2)) if hunk_match.group(2) is not None else 1
            new_start = int(hunk_match.group(3))
            new_count = int(hunk_match.group(4)) if hunk_match.group(4) is not None else 1
            current_hunk = {
                "header": raw_line,
                "old_start": old_start,
                "old_count": old_count,
                "new_start": new_start,
                "new_count": new_count,
                "changed_lines": [],
            }
            files[current_file].append(current_hunk)
            old_line = old_start
            continue

        if current_hunk is None:
            continue

        if raw_line.startswith("---") or raw_line.startswith("+++"):
            continue
        if raw_line.startswith("-"):
            current_hunk["changed_lines"].append(old_line)
            old_line += 1
        elif raw_line.startswith("+"):
            if old_line not in current_hunk["changed_lines"]:
                current_hunk["changed_lines"].append(old_line)
        elif raw_line.startswith(" "):
            old_line += 1

    return files


def score_tc1_diff(
    agent_diff: str,
    upstream_diff: str,
    *,
    target_file: str = TARGET_FILE,
    tolerance: int = LINE_TOLERANCE,
) -> dict[str, Any]:
    """Compare agent_diff against upstream_diff for hunk overlap in target_file (±tolerance lines)."""
    upstream_by_file = parse_unified_diff(upstream_diff)
    agent_by_file = parse_unified_diff(agent_diff)

    upstream_hunks = upstream_by_file.get(target_file, [])
    agent_hunks = agent_by_file.get(target_file, [])
    agent_changed_lines = sorted(
        {line for hunk in agent_hunks for line in hunk["changed_lines"]}
    )

    hunk_results: list[dict[str, Any]] = []
    matched_count = 0
    for idx, u_hunk in enumerate(upstream_hunks, start=1):
        u_lines = sorted(set(u_hunk["changed_lines"]))
        overlapping_agent_lines = sorted(
            {
                a_line
                for a_line in agent_changed_lines
                if any(abs(a_line - u_line) <= tolerance for u_line in u_lines)
            }
        )
        hit = bool(overlapping_agent_lines)
        if hit:
            matched_count += 1
        hunk_results.append(
            {
                "hunk": idx,
                "header": u_hunk["header"],
                "upstream_lines": u_lines,
                "matched": hit,
                "matched_agent_lines": overlapping_agent_lines,
            }
        )

    total_hunks = len(upstream_hunks)
    touched_files = set(agent_by_file.keys())
    summary = (
        f"TC1 hunk overlap on {target_file}: {matched_count}/{total_hunks} upstream hunks matched "
        f"(±{tolerance} lines); files touched: {', '.join(sorted(touched_files)) or 'none'}"
    )
    return {
        "target_file": target_file,
        "upstream_commit": UPSTREAM_COMMIT,
        "tolerance_lines": tolerance,
        "files_touched": sorted(touched_files),
        "upstream_files_hit": sorted(touched_files & UPSTREAM_FILES),
        "extra_files": sorted(touched_files - UPSTREAM_FILES),
        "hunks_total": total_hunks,
        "hunks_matched": matched_count,
        "hunk_overlap_ratio": (matched_count / total_hunks) if total_hunks else 0.0,
        "all_hunks_matched": total_hunks > 0 and matched_count == total_hunks,
        "hunks": hunk_results,
        "summary": summary,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Host-side TC1 hunk-overlap scorer against upstream commit a62e60b5."
    )
    parser.add_argument("agent_diff", type=Path, help="Path to the agent's unified diff file")
    parser.add_argument(
        "--upstream-diff",
        type=Path,
        default=None,
        help="Optional local path to upstream diff (fetches a62e60b5 from GitHub mirror if omitted)",
    )
    parser.add_argument(
        "--tolerance",
        type=int,
        default=LINE_TOLERANCE,
        help="Line number tolerance for hunk overlap (default: 3)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Optional path to write the JSON report",
    )
    args = parser.parse_args(argv)

    agent_diff_text = args.agent_diff.read_text(encoding="utf-8")
    upstream_diff_text = (
        args.upstream_diff.read_text(encoding="utf-8")
        if args.upstream_diff is not None
        else fetch_upstream_diff()
    )
    result = score_tc1_diff(
        agent_diff_text,
        upstream_diff_text,
        tolerance=args.tolerance,
    )
    if args.out is not None:
        args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    print(result["summary"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
