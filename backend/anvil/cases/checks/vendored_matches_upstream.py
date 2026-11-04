#!/usr/bin/env python3
"""Acceptance check: the vendored copy of a component equals its upstream release (ANVIL-44).

Runs inside the sandbox after the agent has finished, uploaded fresh by the host (the agent never
sees or edits it). Stdlib only: the sandbox has python3, nothing else is assumed.

Ground truth is pinned by commit, never by a name the agent can move: the case gives the upstream
tag's commit (--tag-sha) and the base commit (--base-commit). A retagged submodule or a repointed
remote-tracking ref fails the check instead of feeding it (devin's review of #60).

Rules, for every file in the import manifest:

1. Read as if MARKER (e.g. __rtems__) were undefined, the vendored file equals the file at the pinned
   upstream commit, ignoring trailing whitespace, blank lines and labels after #else/#endif.
2. The local patches survive: no file has fewer MARKER blocks than at the base commit, unless the
   case lists it in --allow-dropped (patches retired on purpose; upstream's own 2.2.1 upgrade retired
   two). A re-import that silently drops the patches would otherwise pass rule 1.
3. No new MARKER-only code: lines compiled only when MARKER is defined must already be patch lines at
   the base commit, unless the case lists the file in --allow-changed-patches. Without this, keeping
   the old body under `#ifdef MARKER` and the new one under `#else` passes rules 1-2 while the real
   build still compiles the old code.

Also: the submodule sits at the pinned commit, and the manifest has not dropped files that the base
manifest listed and the pinned upstream still has.

Known limits (both fail closed: a false failure, never a false pass): `#elif` and
`#if defined(MARKER)` are not treated as marker blocks. Residual risk: git does not re-verify object
hashes on read, so an agent that forged objects under a pinned SHA in its own .git could still fool
this; closing that means computing the expectations on the host. The failures seen so far are
sloppiness (TC2 run 7), not forgery.

    python3 vendored_matches_upstream.py --manifest file-import.json --vendored lwip \
        --upstream lwip-upstream --tag STABLE-2_2_1_RELEASE --tag-sha 77dcd25a... \
        --marker __rtems__ --base-commit def203bd...

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
_DIRECTIVE_LABEL = re.compile(r"^\s*(#\s*(?:endif|else))\b.*$")


def git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True)


def strip_marker(text: str, marker: str, defined: bool = False) -> str:
    """The text a compiler sees with `marker` undefined (or defined), touching only `marker` directives."""
    ifdef = re.compile(rf"^\s*#\s*ifdef\s+{re.escape(marker)}\b")
    ifndef = re.compile(rf"^\s*#\s*ifndef\s+{re.escape(marker)}\b")
    if_any = re.compile(r"^\s*#\s*if")
    els = re.compile(r"^\s*#\s*else\b")
    endif = re.compile(r"^\s*#\s*endif\b")
    out: list[str] = []
    # Stack entries: (is_marker_block, keeping_now). Non-marker #if blocks are kept verbatim.
    stack: list[tuple[bool, bool]] = []

    def keeping() -> bool:
        return all(k for _, k in stack)

    for line in text.splitlines(keepends=True):
        if ifdef.match(line):
            stack.append((True, defined))
            continue
        if ifndef.match(line):
            stack.append((True, not defined))
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
    """Lines without trailing whitespace, blank lines dropped, labels after #else/#endif dropped. The
    project's pre-commit hooks strip trailing whitespace, its patches add blank lines around their
    blocks and move those labels; none of that can hide a stale file, because every other line must
    still match."""
    out = []
    for line in text.splitlines():
        if not line.strip():
            continue
        m = _DIRECTIVE_LABEL.match(line)
        out.append(m.group(1) if m else line.rstrip())
    return out


def strip_c_comments(text: str) -> str:
    """The text with C comments removed and line breaks kept, tracking state across lines.

    Whether `foo */` is code depends on the lines before it, so this is a scan, not a per-line regex
    (devin's review of #63): `stale_call(); /* */` keeps `stale_call();`, `*p = f();` stays code, and
    comment markers inside string or character literals are text.
    """
    out: list[str] = []
    i, n = 0, len(text)
    state = "code"  # code | line | block | str | chr
    while i < n:
        c, nxt = text[i], text[i + 1] if i + 1 < n else ""
        if state == "code":
            if c == "/" and nxt == "/":
                state, i = "line", i + 2
                continue
            if c == "/" and nxt == "*":
                state, i = "block", i + 2
                out.append(" ")
                continue
            if c in "\"'":
                state = "str" if c == '"' else "chr"
            out.append(c)
        elif state == "line":
            if c == "\n":
                state = "code"
                out.append(c)
        elif state == "block":
            if c == "*" and nxt == "/":
                state, i = "code", i + 2
                continue
            if c == "\n":
                out.append(c)
        else:  # inside a string or character literal
            out.append(c)
            if c == "\\" and nxt:
                out.append(nxt)
                i += 2
                continue
            if (state == "str" and c == '"') or (state == "chr" and c == "'") or c == "\n":
                state = "code"
        i += 1
    return "".join(out)


def marker_only_lines(text: str, marker: str) -> set[str]:
    """Code lines compiled only when `marker` is defined, comments removed first: a comment doesn't
    compile, so it can't hide old code (TC2 run 8 added a comment next to an existing patch), while
    code with a comment on the same line is still code."""
    code = strip_c_comments(text)
    return set(normalize(strip_marker(code, marker, defined=True))) - set(normalize(strip_marker(code, marker)))


def marker_blocks(text: str, marker: str) -> int:
    """How many `#ifdef MARKER` / `#ifndef MARKER` blocks a file has."""
    return len(re.findall(rf"^\s*#\s*if(?:n?def)\s+{re.escape(marker)}\b", text, re.M))


def manifest_files(raw: bytes) -> list[str]:
    return list(json.loads(raw)["files-to-import"])


def decode(raw: bytes) -> str:
    return raw.decode("utf-8", "surrogateescape")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=".")
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--vendored", required=True)
    ap.add_argument("--upstream", required=True)
    ap.add_argument("--tag", required=True, help="upstream tag name (for messages and the tag check)")
    ap.add_argument("--tag-sha", required=True, help="the commit the tag must point at (pinned by the case)")
    ap.add_argument("--marker", required=True)
    ap.add_argument("--base-commit", required=True, help="pinned base commit (the case's base.sha)")
    ap.add_argument("--allow-dropped", nargs="*", default=[],
                    help="manifest files whose local patches may be retired (declared by the case)")
    ap.add_argument("--allow-changed-patches", nargs="*", default=[],
                    help="manifest files whose local patches may gain new lines (declared by the case)")
    a = ap.parse_args()
    repo = Path(a.repo).resolve()
    upstream = repo / a.upstream
    sha = a.tag_sha
    problems: list[str] = []

    if git("cat-file", "-e", f"{sha}^{{commit}}", cwd=upstream).returncode != 0:
        problems.append(f"upstream commit {sha[:12]} ({a.tag}) is not available in {a.upstream}")
    tagged = git("rev-parse", f"{a.tag}^{{commit}}", cwd=upstream).stdout.decode().strip()
    if tagged and tagged != sha:
        problems.append(f"{a.upstream}: tag {a.tag} points at {tagged[:12]}, not the pinned {sha[:12]}")
    if git("rev-parse", "HEAD", cwd=upstream).stdout.decode().strip() != sha:
        problems.append(f"{a.upstream} is not checked out at {a.tag} ({sha[:12]})")
    if git("cat-file", "-e", f"{a.base_commit}^{{commit}}", cwd=repo).returncode != 0:
        problems.append(f"base commit {a.base_commit[:12]} is not available")

    files = manifest_files((repo / a.manifest).read_bytes())
    base_manifest = git("show", f"{a.base_commit}:{a.manifest}", cwd=repo)
    base_files = manifest_files(base_manifest.stdout) if base_manifest.returncode == 0 else []
    for f in base_files:
        if f not in files and git("cat-file", "-e", f"{sha}:{f}", cwd=upstream).returncode == 0:
            problems.append(f"manifest dropped {f}, which {a.tag} still has")

    checked = 0
    for f in files:
        up = git("show", f"{sha}:{f}", cwd=upstream)
        if up.returncode != 0:
            problems.append(f"{f}: not in upstream {a.tag}")
            continue
        vendored = repo / a.vendored / f
        if not vendored.is_file():
            problems.append(f"{a.vendored}/{f}: missing")
            continue
        mine = decode(vendored.read_bytes())
        checked += 1
        if normalize(strip_marker(mine, a.marker)) != normalize(decode(up.stdout)):
            problems.append(f"{a.vendored}/{f}: differs from {a.tag} outside {a.marker} blocks")
        old = git("show", f"{a.base_commit}:{a.vendored}/{f}", cwd=repo)
        if old.returncode != 0:
            continue
        base_text = decode(old.stdout)
        before, after = marker_blocks(base_text, a.marker), marker_blocks(mine, a.marker)
        if after < before and f not in a.allow_dropped:
            problems.append(f"{a.vendored}/{f}: {a.marker} local patches lost ({before} blocks on the base, {after} now)")
        new = marker_only_lines(mine, a.marker) - marker_only_lines(base_text, a.marker)
        if new and f not in a.allow_changed_patches:
            problems.append(f"{a.vendored}/{f}: {len(new)} new {a.marker}-only lines that are not local patches on "
                            f"the base (code compiled only for {a.marker} must not replace upstream code)")

    ok = not problems
    print(json.dumps({"ok": ok, "checked": checked, "files": len(files),
                      "problems": problems[:MAX_PROBLEMS], "more_problems": max(0, len(problems) - MAX_PROBLEMS)}))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
