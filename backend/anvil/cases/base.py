"""Build a case's base branch: the repo just before the fix, plus the factory's infra files.

Pure git plumbing on the host's bare clone (the same one git_push uses), no checkout: the
tree of ``<first fix>^`` with ``overlay.paths`` taken from ``overlay.ref``, committed on top of
``<first fix>^``. The result is deterministic: building twice gives the same tree, and an
existing base can be checked against a rebuild (``same_tree``).
"""

from __future__ import annotations

import base64
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from anvil.tools.errors import ToolError
from anvil.tools.git_push import PushSettings
from anvil.tools.github import full_name

from .spec import Case


def _git(args: list[str], cwd: Path | None, s: PushSettings, *, env: dict[str, str] | None = None,
         stdin: str | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    full_env = dict(os.environ, GIT_TERMINAL_PROMPT="0", **(env or {}))
    if s.github_token:
        basic = base64.b64encode(f"x-access-token:{s.github_token}".encode()).decode()
        full_env |= {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.extraHeader",
                     "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}"}
    # Bytes, not text mode: on Windows text-mode stdin turns LF into CRLF, and
    # update-index --index-info would then read paths ending in a carriage return.
    raw = subprocess.run(["git", *args], cwd=cwd, env=full_env, capture_output=True, timeout=900,
                         input=stdin.encode("utf-8") if stdin is not None else None)
    r = subprocess.CompletedProcess(raw.args, raw.returncode, raw.stdout.decode("utf-8", "replace"),
                                    raw.stderr.decode("utf-8", "replace"))
    if check and r.returncode:
        out = (r.stdout + r.stderr).replace(s.github_token or "\0", "***")
        raise ToolError(f"git {args[0]} failed ({r.returncode}): {out[-1500:]}")
    return r


def host_clone(repo: str, s: PushSettings | None = None) -> Path:
    """The bare, blobless clone under the anvil repos dir, fetched to match the remote."""
    s = s or PushSettings()
    name = full_name(repo)
    clone = Path(s.anvil_repos_dir) / name.replace("/", "__")
    remote = f"{s.github_remote_base.rstrip('/')}/{name}.git"
    if not (clone / "HEAD").exists():
        clone.parent.mkdir(parents=True, exist_ok=True)
        _git(["clone", "--bare", "--filter=blob:none", remote, str(clone)], None, s)
    else:
        _git(["fetch", "--prune", remote, "+refs/heads/*:refs/heads/*"], clone, s)
    return clone


def build_base(case: Case, *, push: bool = False, clone: Path | None = None,
               s: PushSettings | None = None) -> dict[str, Any]:
    s = s or PushSettings()
    clone = clone or host_clone(case.repo, s)
    rev = lambda ref: _git(["rev-parse", "--verify", f"{ref}^{{commit}}"], clone, s).stdout.strip()  # noqa: E731
    parent = rev(f"{case.fix_commits[0]}^")

    with tempfile.TemporaryDirectory() as tmp:
        index = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
        _git(["read-tree", parent], clone, s, env=index)
        overlaid: list[str] = []
        if case.overlay:
            overlay_commit = rev(case.overlay.ref)
            # Drop whatever the parent has under the overlay paths, then take the infra ref's version.
            _git(["rm", "-r", "--cached", "--ignore-unmatch", "-q", "--", *case.overlay.paths], clone, s, env=index)
            listing = _git(["ls-tree", "-r", "--full-tree", overlay_commit, "--", *case.overlay.paths],
                           clone, s).stdout
            if not listing.strip():
                raise ToolError(f"overlay ref {case.overlay.ref} has none of {list(case.overlay.paths)}")
            _git(["update-index", "--index-info"], clone, s, env=index, stdin=listing)
            overlaid = sorted({line.split("\t", 1)[1] for line in listing.splitlines()})
        tree = _git(["write-tree"], clone, s, env=index).stdout.strip()

    existing = _git(["rev-parse", "--verify", "-q", f"refs/heads/{case.base_branch}"], clone, s, check=False)
    existing_sha = existing.stdout.strip() if existing.returncode == 0 else None
    existing_tree = (_git(["rev-parse", f"{existing_sha}^{{tree}}"], clone, s).stdout.strip()
                     if existing_sha else None)
    result: dict[str, Any] = {
        "case": case.id, "repo": full_name(case.repo), "branch": case.base_branch, "parent": parent,
        "tree": tree, "overlaid_files": overlaid, "existing_sha": existing_sha,
        "same_tree": existing_tree == tree if existing_tree else None, "pushed": None,
    }
    if existing_sha:
        return result  # never move an existing base: runs and scores refer to it
    meta = {"GIT_AUTHOR_NAME": "Anvil", "GIT_AUTHOR_EMAIL": "anvil@localhost",
            "GIT_COMMITTER_NAME": "Anvil", "GIT_COMMITTER_EMAIL": "anvil@localhost"}
    message = (f"{case.id}: base for the bug-replay case\n\nThe tree of {parent[:12]} (parent of the "
               f"upstream fix) plus the factory's infrastructure files from {case.overlay.ref if case.overlay else '-'}.\n")
    commit = _git(["commit-tree", tree, "-p", parent, "-m", message], clone, s, env=meta).stdout.strip()
    result["commit"] = commit
    if push:
        remote = f"{s.github_remote_base.rstrip('/')}/{full_name(case.repo)}.git"
        _git(["push", remote, f"{commit}:refs/heads/{case.base_branch}"], clone, s)
        _git(["update-ref", f"refs/heads/{case.base_branch}", commit], clone, s)
        result["pushed"] = True
    return result
