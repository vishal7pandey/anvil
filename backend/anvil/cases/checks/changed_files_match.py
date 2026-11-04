#!/usr/bin/env python3
"""Acceptance check: the agent's change includes at least one file matching a pattern (ANVIL-44).

For a defect-fix: a regression test must be part of the change (`--pattern 'testsuites/*'`).
Compares the committed HEAD with the pinned base commit (not a ref name: the agent can move refs, and
a merge-base against an unrelated branch would sweep in upstream's own test history). Uncommitted
edits do not count. Presence only: the scorer judges the test's quality.

    python3 changed_files_match.py --base-commit dbb32b7b... --pattern 'testsuites/*' --what "regression test"
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import subprocess
import sys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-commit", required=True)
    ap.add_argument("--pattern", required=True, action="append")
    ap.add_argument("--what", default="matching file")
    a = ap.parse_args()
    out = subprocess.run(["git", "diff", "--name-only", a.base_commit, "HEAD"], capture_output=True, text=True)
    if out.returncode != 0:
        print(json.dumps({"ok": False, "problems": [f"git diff against {a.base_commit[:12]} failed: {out.stderr.strip()}"]}))
        return 1
    changed = [f for f in out.stdout.splitlines() if f]
    hits = [f for f in changed if any(fnmatch.fnmatch(f, p) for p in a.pattern)]
    ok = bool(hits)
    problems = [] if ok else [f"the committed change has no {a.what} (no file matches {', '.join(a.pattern)}; "
                              f"{len(changed)} files changed)"]
    print(json.dumps({"ok": ok, "matched": hits[:20], "problems": problems}))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
