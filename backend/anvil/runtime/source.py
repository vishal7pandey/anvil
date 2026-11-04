"""Where a run's code comes from, and where its result goes (ANVIL-55).

A run's `repo` is either a GitHub `owner/name` (cloned in the sandbox, pushed, PR'd) or an absolute
path to a git working copy on the host: the offline path for laptops that cannot reach GitHub.

Local source, in:
    The host makes a shallow single-branch copy of the base branch (depth 200, the same as the GitHub
    checkout) and copies only that into the sandbox. The sandbox never sees the host folder: a
    working copy on a laptop may hold other branches, including one with a withheld fix, and a bind
    mount would hand the agent all of them. The sandbox has no network at all.

Local source, out:
    No push, no PR. The host writes `<run_id>.patch` (git format-patch base..branch, applies with
    `git am`) and `<run_id>.bundle` (fetchable) to `private/runs/<run_id>/` (gitignored).

Contract with the local tracker (ANVIL-56, devin): a ticket says `Code base: branch <base> of <path>`,
intake copies `<path>` into `Run.repo` verbatim. Paths must be absolute and contain no spaces.
"""

from __future__ import annotations

from anvil.runtime.sandbox import host_run
import io
import os
import re
import shlex
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

_WINDOWS_ABS = re.compile(r"^[A-Za-z]:[\\/]")
_NAME = re.compile(r"[A-Za-z0-9_.-]+")
REPO_ROOT = Path(__file__).resolve().parents[3]
LOCAL_DEPTH = int(os.environ.get("ANVIL_LOCAL_DEPTH", "200"))


def is_local(repo: str) -> bool:
    """An absolute host path (C:\\x, C:/x or /x), as opposed to a GitHub owner/name."""
    return isinstance(repo, str) and (bool(_WINDOWS_ABS.match(repo)) or repo.startswith("/"))


def local_name(repo: str) -> str:
    """The checkout directory name for a local source: the folder's basename."""
    path = PureWindowsPath(repo) if _WINDOWS_ABS.match(repo) else PurePosixPath(repo)
    name = path.name
    if not _NAME.fullmatch(name or "") or name in (".", ".."):
        raise ValueError(f"invalid local repository {repo!r}: the folder name must be letters, digits, '_', '.' or '-'")
    return name


def validate_local(repo: str, *, check_exists: bool = True) -> str:
    """Absolute, no spaces or shell metacharacters, a sane folder name, and (on the host) a git repo."""
    repo = repo.strip().rstrip("\\/")
    if not is_local(repo):
        raise ValueError(f"invalid local repository {repo!r}: expected an absolute path like C:\\work\\rtems")
    if re.search(r"[\s'\"`$;&|<>*?]", repo) or ".." in re.split(r"[\\/]", repo):
        raise ValueError(f"invalid local repository {repo!r}: no spaces, quotes, '..' or shell characters")
    local_name(repo)
    if check_exists and not (Path(repo) / ".git").exists():
        raise ValueError(f"{repo} is not a git working copy on this machine (no .git folder)")
    return repo


def runs_dir(run_id: str) -> Path:
    """Host folder for a run's exported result: private/runs/<run_id>/ (gitignored)."""
    base = Path(os.environ.get("ANVIL_RUNS_DIR") or REPO_ROOT / "private" / "runs")
    safe = re.sub(r"[^A-Za-z0-9_.-]", "", str(run_id))
    out = base / safe
    out.mkdir(parents=True, exist_ok=True)
    return out


def _git(args: list[str], cwd: Path | None = None) -> str:
    res = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:3])} failed: {(res.stderr or res.stdout).strip()[-600:]}")
    return res.stdout.strip()


def _base_ref(repo: str, base: str) -> str:
    """The ref to copy: the local branch if it exists, else the remote-tracking origin/<base>."""
    for ref in (f"refs/heads/{base}", f"refs/remotes/origin/{base}"):
        if subprocess.run(["git", "-C", repo, "rev-parse", "--verify", "--quiet", ref + "^{commit}"],
                          capture_output=True).returncode == 0:
            return ref
    raise RuntimeError(f"branch {base!r} not found in {repo} (neither {base} nor origin/{base}); "
                       f"check the ticket's 'Code base: branch <base> of <path>' line")


def local_copy_tar(repo: str, base: str) -> bytes:
    """A tar of /<name>/.git holding a shallow single-branch repo of `base`, for put_archive at /workspace.

    Only the git directory travels: the sandbox builds the working tree itself (`local_checkout_script`),
    so file modes (executable scripts like waf) and LF line endings come from git, not from a Windows
    checkout. Inside, the base commit sits at refs/remotes/origin/<base> (what verify, lint and the
    agent's `git diff origin/<base>` expect) and there is no remote to fetch from.
    """
    repo = validate_local(repo)
    name, ref = local_name(repo), _base_ref(repo, base)
    with tempfile.TemporaryDirectory(prefix="anvil-src-") as tmp:
        dest = Path(tmp) / name
        _git(["init", "--quiet", str(dest)])
        _git(["fetch", "--quiet", "--no-tags", f"--depth={LOCAL_DEPTH}", Path(repo).resolve().as_uri(),
              f"+{ref}:refs/remotes/origin/{base}"], cwd=dest)
        _git(["update-ref", f"refs/heads/{base}", f"refs/remotes/origin/{base}"], cwd=dest)
        _git(["symbolic-ref", "HEAD", f"refs/heads/{base}"], cwd=dest)
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            # Member names built by hand: tarfile's recursive add joins with os.sep, so on Windows
            # the sandbox would receive files literally named ".git\HEAD".
            gitdir = dest / ".git"
            tar.add(dest, arcname=name, recursive=False)  # else Docker creates it root-owned
            tar.add(gitdir, arcname=f"{name}/.git", recursive=False)
            for item in sorted(gitdir.rglob("*")):
                tar.add(item, arcname=f"{name}/.git/{item.relative_to(gitdir).as_posix()}", recursive=False)
        return buf.getvalue()


def local_checkout_script(path: str, base: str, branch: str) -> str:
    """Build the working tree from the copied .git with Linux settings, then make the work branch."""
    q = shlex.quote
    return (f"set -e; cd {q(path)}; "
            # git init on Windows writes these; on Linux they would mangle symlinks and file modes.
            f"git config core.symlinks true; git config core.filemode true; git config core.ignorecase false; "
            f"git config core.autocrlf false; git config user.name anvil-bot; "
            f"git config user.email anvil-bot@users.noreply.github.com; "
            f"git reset --quiet --hard {q('refs/heads/' + base)}; "
            f"git switch --quiet {q(branch)} 2>/dev/null || git switch --quiet -c {q(branch)}; "
            f"echo BASE_SHA=$(git rev-parse origin/{q(base)}); echo HEAD_SHA=$(git rev-parse HEAD)")


def export_result(sandbox: Any, run: dict[str, Any], path: str, branch: str) -> dict[str, Any]:
    """Write the branch's commits since the base to private/runs/<run_id>/ as a patch and a bundle.

    Returns {"commits": n, "patch": host path | None, "bundle": host path | None}.
    """
    q = shlex.quote
    base = q(f"origin/{run.get('base_ref') or 'main'}")
    tmp = "/tmp/anvil-export"
    res = host_run(sandbox, 
        f"cd {q(path)} && rm -rf {tmp} && mkdir -p {tmp} && n=$(git rev-list --count {base}..{q(branch)}) && "
        f"echo COMMITS=$n && if [ \"$n\" -gt 0 ]; then "
        f"git format-patch --quiet --stdout {base}..{q(branch)} > {tmp}/result.patch && "
        f"git bundle create --quiet {tmp}/result.bundle {q(branch)} ^{base}; fi", timeout=300)
    found = re.search(r"COMMITS=(\d+)", res.output or "")
    if res.exit_code != 0 or not found:
        raise RuntimeError(f"export failed (exit {res.exit_code}): {(res.output or '').strip()[-600:]}")
    commits = int(found.group(1))
    out: dict[str, Any] = {"commits": commits, "patch": None, "bundle": None}
    if commits == 0:
        return out
    target = runs_dir(run["run_id"])
    for kind, ext in (("patch", "patch"), ("bundle", "bundle")):
        got = sandbox.download_files([f"{tmp}/result.{ext}"])[0]
        if got.error or got.content is None:
            raise RuntimeError(f"could not download the {kind} from the sandbox ({got.error})")
        dest = target / f"{run['run_id']}.{ext}"
        dest.write_bytes(got.content)
        out[kind] = str(dest)
    return out
