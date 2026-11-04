#!/usr/bin/env python3
"""Acceptance check: the vendored copy of a component equals its upstream release (ANVIL-44).

Runs inside the sandbox after the agent has finished, uploaded fresh by the host (the agent never
sees or edits it). Stdlib only: the sandbox has python3, nothing else is assumed.

Rule: for every file in the import manifest, the vendored file read as if MARKER (e.g. __rtems__) were
undefined must be byte-identical to the file at the upstream tag. Local patches are allowed only
inside `#ifdef MARKER ... [#else ...] #endif` / `#ifndef MARKER ... [#else ...] #endif` blocks, so
they vanish under that reading; any other difference is a real one (TC2 run 7 changed only the
submodule pointer and a version header, and left the 2.1.3 sources in place).

Also: the submodule must sit at the tag; the manifest must not have dropped files that the base
manifest listed and the new upstream still has (shrinking the manifest would hide stale files); and no
file may have fewer MARKER blocks than on the base (a re-import that silently drops the local patches
passes the equality rule) unless the case lists it in --allow-dropped (patches retired on purpose).

    python3 vendored_matches_upstream.py --repo . --manifest file-import.json --vendored lwip \
        --upstream lwip-upstream --tag STABLE-2_2_1_RELEASE --marker __rtems__ \
        --base-ref origin/tc2/upgrade-base-leon3

Prints one JSON object {"ok": bool, "checked": n, "problems": [...]} and exits 0 when ok.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

MAX_PROBLEMS = 25


def git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True)


def strip_marker(text: str, marker: str) -> str:
    """The text a compiler sees with `marker` undefined, touching only directives about `marker`."""
    ifdef = re.compile(rf"^\s*#\s*ifdef\s+{re.escape(marker)}\b")
    ifndef = re.compile(rf"^\s*#\s*ifndef\s+{re.escape(marker)}\b")
    if_any = re.compile(r"^\s*#\s*if")
    els = re.compile(r"^\s*#\s*else\b")
    endif = re.compile(r"^\s*#\s*endif\b")
    out: list[str] = []
    # Stack entries: (is_marker_block, keeping_now). Non-marker #if blocks inherit and are kept verbatim.
    stack: list[tuple[bool, bool]] = []

    def keeping() -> bool:
        return all(k for _, k in stack)

    for line in text.splitlines(keepends=True):
        if ifdef.match(line):
            stack.append((True, False))
            continue
        if ifndef.match(line):
            stack.append((True, True))
            continue
        if if_any.match(line):
            if keeping():
                out.append(line)
            stack.append((False, True))
            continue
        if els.match(line) and stack:
            is_marker, keep = stack[-1]
            if is_marker:
                stack[-1] = (True, not keep)
                continue
        if endif.match(line) and stack:
            is_marker, _ = stack.pop()
            if is_marker:
                continue
            if keeping():
                out.append(line)
            continue
        if keeping():
            out.append(line)
    return "".join(out)


def normalize(text: str) -> list[str]:
    """Lines without trailing whitespace, blank lines dropped. The project's pre-commit hooks strip
    trailing whitespace and its patches add blank lines around their blocks; neither can hide a stale
    file, because every non-blank line must still match. Comments after `#endif`/`#else` are dropped
    too: patches that wrap upstream code move those labels around (lwIP's sockets.h)."""
    out = []
    for line in text.splitlines():
        if not line.strip():
            continue
        m = _DIRECTIVE_LABEL.match(line)
        out.append(m.group(1) if m else line.rstrip())
    return out


_DIRECTIVE_LABEL = re.compile(r"^\s*(#\s*(?:endif|else))\b.*$")


def marker_blocks(text: str, marker: str) -> int:
    """How many `#ifdef MARKER` / `#ifndef MARKER` blocks a file has."""
    return len(re.findall(rf"^\s*#\s*if(?:n?def)\s+{re.escape(marker)}\b", text, re.M))


def manifest_files(raw: bytes) -> list[str]:
    return list(json.loads(raw)["files-to-import"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=".")
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--vendored", required=True)
    ap.add_argument("--upstream", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--marker", required=True)
    ap.add_argument("--base-ref", help="ref whose manifest the new one must not shrink")
    ap.add_argument("--allow-dropped", nargs="*", default=[],
                    help="manifest files whose local patches may be retired (declared by the case)")
    a = ap.parse_args()
    repo = Path(a.repo).resolve()
    upstream = repo / a.upstream
    problems: list[str] = []

    tag_sha = git("rev-parse", f"{a.tag}^{{commit}}", cwd=upstream)
    head_sha = git("rev-parse", "HEAD", cwd=upstream)
    if tag_sha.returncode != 0:
        problems.append(f"upstream tag {a.tag} is not available in {a.upstream}")
    elif head_sha.stdout.strip() != tag_sha.stdout.strip():
        problems.append(f"{a.upstream} is not checked out at {a.tag}")

    files = manifest_files((repo / a.manifest).read_bytes())
    if a.base_ref:
        base = git("show", f"{a.base_ref}:{a.manifest}", cwd=repo)
        if base.returncode == 0:
            for f in manifest_files(base.stdout):
                if f not in files and git("cat-file", "-e", f"{a.tag}:{f}", cwd=upstream).returncode == 0:
                    problems.append(f"manifest dropped {f}, which {a.tag} still has")
                # The local patches must survive: a plain re-import that drops them passes the
                # equality rule below (TC2 runs 3-5 lost the __rtems__ patches exactly that way).
                old = git("show", f"{a.base_ref}:{a.vendored}/{f}", cwd=repo)
                vendored_now = repo / a.vendored / f
                if old.returncode == 0 and vendored_now.is_file():
                    before = marker_blocks(old.stdout.decode("utf-8", "surrogateescape"), a.marker)
                    after = marker_blocks(vendored_now.read_bytes().decode("utf-8", "surrogateescape"), a.marker)
                    if after < before and f not in a.allow_dropped:
                        problems.append(f"{a.vendored}/{f}: {a.marker} local patches lost "
                                        f"({before} blocks on the base, {after} now)")

    checked = 0
    for f in files:
        up = git("show", f"{a.tag}:{f}", cwd=upstream)
        if up.returncode != 0:
            problems.append(f"{f}: not in upstream {a.tag}")
            continue
        vendored = repo / a.vendored / f
        if not vendored.is_file():
            problems.append(f"{a.vendored}/{f}: missing")
            continue
        mine = strip_marker(vendored.read_bytes().decode("utf-8", "surrogateescape"), a.marker)
        theirs = up.stdout.decode("utf-8", "surrogateescape")
        checked += 1
        if normalize(mine) != normalize(theirs):
            problems.append(f"{a.vendored}/{f}: differs from {a.tag} outside {a.marker} blocks")

    ok = not problems
    print(json.dumps({"ok": ok, "checked": checked, "files": len(files),
                      "problems": problems[:MAX_PROBLEMS], "more_problems": max(0, len(problems) - MAX_PROBLEMS)}))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
