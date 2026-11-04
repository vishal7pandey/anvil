"""git_push: move the agent's commits out of the sandbox and push them from the host (ADR-004).

Inside the sandbox: `git bundle create` of the branch minus what origin already has. On the host: a
blobless clone under %LOCALAPPDATA%/anvil/repos/<repo>, fetch the bundle, push with the token passed
through git's environment config (never argv, never the sandbox).
"""

import base64
import os
import re
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Protocol

from anvil.tools.errors import ToolError
from anvil.tools.github import GitHubSettings, full_name, is_protected
from anvil.tools.locks import acquire_lock

_BRANCH = re.compile(r"^anvil/[A-Za-z0-9._/-]+$")


class Sandbox(Protocol):
    """The part of anvil.runtime.sandbox.DockerSandbox (deepagents BaseSandbox) that git_push uses."""

    def execute(self, command: str, *, timeout: int | None = None) -> Any: ...
    def download_files(self, paths: list[str]) -> list[Any]: ...


class PushSettings(GitHubSettings):
    anvil_repos_dir: str = str(Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local/share") / "anvil" / "repos")


def _host_git(args: list[str], cwd: Path | None, s: PushSettings) -> str:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    if s.github_token:
        basic = base64.b64encode(f"x-access-token:{s.github_token}".encode()).decode()
        env |= {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.extraHeader", "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}"}
    r = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=900)
    out = (r.stdout + r.stderr).replace(s.github_token or "\0", "***")
    if r.returncode:
        raise ToolError(f"host git {args[0]} failed ({r.returncode}): {out[-1500:]}")
    return out


def _protected_paths(clone: Path, ref: str, s: PushSettings) -> list[str]:
    """Paths under .github/ changed by commits on `ref` that no other branch has. Runs on the host clone, not the sandbox."""
    others = [r for r in _host_git(["for-each-ref", "--format=%(refname)", "refs/heads/"], clone, s).split() if r != ref]
    r = subprocess.run(["git", "rev-list", "--stdin", ref], cwd=clone, input="".join(f"^{o}\n" for o in others), capture_output=True, text=True, timeout=300)
    if r.returncode:
        raise ToolError(f"refused: could not list the commits being pushed: {r.stderr[-500:]}")
    commits = r.stdout
    if not commits.strip():
        return []
    r = subprocess.run(["git", "diff-tree", "--stdin", "--root", "-r", "--cc", "--no-commit-id", "--name-only"], cwd=clone, input=commits, capture_output=True, text=True, timeout=300)
    if r.returncode:
        raise ToolError(f"refused: could not inspect the commits being pushed: {r.stderr[-500:]}")
    return sorted({p for p in r.stdout.splitlines() if is_protected(p)})


def push_from_sandbox(sandbox: Sandbox, repo: str, branch: str, workspace: str = "/workspace") -> dict[str, Any]:
    if not _BRANCH.match(branch) or ".." in branch or branch in {"anvil/main", "main"}:
        raise ToolError(f"refused: can only push 'anvil/<key>-<slug>' branches, got '{branch}'")
    s, name = PushSettings(), full_name(repo)
    src = f"{workspace}/{repo.split('/')[-1]}"
    bundle = f"{workspace}/.anvil-push.bundle"
    ref = f"refs/heads/{branch}"

    res = sandbox.execute(f"git -C {shlex.quote(src)} rev-parse --verify {shlex.quote(ref)}")
    if res.exit_code:
        raise ToolError(f"branch '{branch}' not found in the sandbox repo {src}: {res.output[-500:]}")
    sha = res.output.strip().splitlines()[-1]
    res = sandbox.execute(f"rm -f {bundle} && git -C {shlex.quote(src)} bundle create {bundle} {shlex.quote(ref)} --not --remotes=origin")
    if res.exit_code:
        raise ToolError(f"nothing to push or bundle failed: {res.output[-800:]}")
    [dl] = sandbox.download_files([bundle])
    sandbox.execute(f"rm -f {bundle}")
    if dl.content is None:
        raise ToolError(f"could not download the bundle from the sandbox: {dl.error}")

    # ANVIL-81: the shared host clone is one bare repo per GitHub repo — hold a per-repo lock across
    # the whole fetch -> import -> check -> push sequence or two runs interleave refs/hit *.lock.
    with acquire_lock(f"git-push-{name.replace('/', '__')}", what=f"git_push {ref} to {name}"):
        clone = Path(s.anvil_repos_dir) / name.replace("/", "__")
        remote = f"{s.github_remote_base.rstrip('/')}/{name}.git"
        if not (clone / "HEAD").exists():
            clone.parent.mkdir(parents=True, exist_ok=True)
            with acquire_lock("network-heavy", what=f"first host clone of {name} for git_push"):
                _host_git(["clone", "--bare", "--filter=blob:none", remote, str(clone)], None, s)
        else:
            _host_git(["fetch", "--prune", remote, "+refs/heads/*:refs/heads/*"], clone, s)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "push.bundle"
            path.write_bytes(dl.content)
            _host_git(["fetch", str(path), f"+{ref}:{ref}"], clone, s)
        if blocked := _protected_paths(clone, ref, s):
            _host_git(["update-ref", "-d", ref], clone, s)
            raise ToolError(f"refused: the commits touch protected paths ({', '.join(blocked[:10])}). Agents may not change .github/; drop those changes and commit again.")
        _host_git(["push", remote, f"{ref}:{ref}"], clone, s)
    return {"repo": name, "branch": branch, "sha": sha, "url": f"https://github.com/{name}/tree/{branch}"}


def make_git_push(sandbox: Sandbox) -> Callable[[str, str], dict[str, Any]]:
    """Bind a session's sandbox; the returned function is the tool the model sees."""

    def git_push(repo: str, branch: str) -> dict[str, Any]:
        """Push your committed `anvil/...` branch from the sandbox repo /workspace/<repo> to GitHub.

        Commit first. Pushes to main, to branches not starting with 'anvil/', or of commits that
        change anything under .github/ are refused.
        """
        return push_from_sandbox(sandbox, repo, branch)

    return git_push
