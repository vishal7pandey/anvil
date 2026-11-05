"""Where a run's code comes from, and where its result goes (ANVIL-55, ANVIL-91).

A run's `repo` is either a GitHub `owner/name` (cloned in the sandbox, pushed, PR'd) or an absolute
path to a project on the host: the offline path for laptops that cannot reach GitHub. Since
ANVIL-91 that project may come in any shape:

- a git working copy, on any branch (default: its current branch);
- a plain folder with no git at all;
- a .zip / .tar.gz / .tgz / .tar archive of either;

and its path may contain spaces. The host path never reaches a shell: it is only used in
subprocess argument lists, and the sandbox sees a safe slug of the folder name.

Local source, in:
    The host normalises every shape to the same thing: a single-branch `.git` of the base, which it
    copies into the sandbox. A working copy is fetched shallow (depth 200) and single-branch, so other
    branches (one may hold a withheld fix) never travel. A plain folder or an archive becomes a
    one-commit snapshot, read in place (the folder is never written to). The sandbox never sees the
    host folder and has no network at all.

Local source, out:
    No push, no PR. The host writes `<run_id>.patch` (git format-patch base..branch: `git am` in a
    working copy, `git apply` in a plain folder) and `<run_id>.bundle` to `private/runs/<run_id>/`.

Contract with the local tracker (ANVIL-56): a ticket says `Code base: branch <base> of <path>`. The
branch is optional for a local path, and a path with spaces is written in double quotes. Intake
copies `<path>` into `Run.repo` verbatim.
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
REPO_ROOT = Path(__file__).resolve().parents[3]
LOCAL_DEPTH = int(os.environ.get("ANVIL_LOCAL_DEPTH", "200"))
ARCHIVE_SUFFIXES = (".tar.gz", ".tgz", ".tar", ".zip")
# Never part of a snapshot of a plain folder: VCS data, dependencies, caches, build output (ANVIL-91).
SNAPSHOT_EXCLUDES = [".git/", "node_modules/", "__pycache__/", "*.pyc", ".venv/", "venv/", "build/",
                     "dist/", ".mypy_cache/", ".pytest_cache/", ".DS_Store", "Thumbs.db"]


def is_local(repo: str) -> bool:
    """An absolute host path (C:\\x, C:/x or /x), as opposed to a GitHub owner/name."""
    return isinstance(repo, str) and (bool(_WINDOWS_ABS.match(repo.strip().strip('"')))
                                      or repo.strip().strip('"').startswith("/"))


def _pure(repo: str) -> PurePosixPath | PureWindowsPath:
    return PureWindowsPath(repo) if _WINDOWS_ABS.match(repo) else PurePosixPath(repo)


def local_name(repo: str) -> str:
    """The sandbox directory name for a local source: a safe slug of the folder (or archive) name."""
    name = _pure(repo.strip().strip('"').rstrip("\\/")).name
    for suffix in ARCHIVE_SUFFIXES:
        if name.lower().endswith(suffix):
            name = name[: -len(suffix)]
            break
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-.") or "project"
    return slug[:64]


def validate_local(repo: str, *, check_exists: bool = True) -> str:
    """An absolute path on this machine to a project in any shape. Spaces are fine; quotes, control
    characters and '..' are not. Surrounding double quotes and trailing separators are dropped."""
    repo = repo.strip().strip('"').rstrip("\\/")
    if not is_local(repo):
        raise ValueError(f"invalid local project {repo!r}: expected an absolute path like C:\\work\\app")
    if re.search(r'[\x00-\x1f"`]', repo) or ".." in re.split(r"[\\/]", repo):
        raise ValueError(f"invalid local project {repo!r}: no quotes, control characters or '..'")
    if check_exists:
        local_kind(repo)  # raises with a clear message when it is not usable
    return repo


def local_kind(repo: str) -> str:
    """'git' (a working copy), 'folder' (plain files) or 'archive'."""
    p = Path(repo)
    if p.is_dir():
        return "git" if (p / ".git").exists() else "folder"
    if p.is_file() and p.name.lower().endswith(ARCHIVE_SUFFIXES):
        return "archive"
    if p.exists():
        raise ValueError(f"{repo} is a file but not a .zip/.tar.gz/.tgz/.tar archive")
    raise ValueError(f"{repo} does not exist on this machine")


def default_base(repo: str) -> str:
    """The branch to work from when none is given: a working copy's current branch, else 'main'
    (the branch a snapshot of a plain folder or an archive is committed on)."""
    if local_kind(repo) != "git":
        return "main"
    res = subprocess.run(["git", "-C", repo, "symbolic-ref", "--quiet", "--short", "HEAD"],
                         capture_output=True, text=True)
    return res.stdout.strip() if res.returncode == 0 and res.stdout.strip() else "main"


def uncommitted_changes(repo: str) -> int:
    """How many files differ from HEAD in a working copy. They are NOT copied (the run works from the
    committed base); the count is recorded on the run so nobody is surprised by a missing edit."""
    if local_kind(repo) != "git":
        return 0
    res = subprocess.run(["git", "-C", repo, "status", "--porcelain"], capture_output=True, text=True)
    return len([line for line in res.stdout.splitlines() if line.strip()]) if res.returncode == 0 else 0


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
                       f"check the ticket's 'Code base:' line or leave the branch out to use the current one")


def _extract_archive(archive: Path, into: Path) -> Path:
    """Unpack safely (no absolute paths, '..', links or devices); return the project root inside."""
    into.mkdir(parents=True, exist_ok=True)
    root = into.resolve()

    def check(name: str) -> None:
        target = (into / name).resolve()
        if name.startswith(("/", "\\")) or ".." in re.split(r"[\\/]", name) or not target.is_relative_to(root):
            raise ValueError(f"refusing archive entry {name!r}")

    if archive.name.lower().endswith(".zip"):
        import zipfile

        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                check(info.filename)
            zf.extractall(into)
    else:
        with tarfile.open(archive) as tf:
            for member in tf.getmembers():
                check(member.name)
                if member.issym() or member.islnk() or member.isdev():
                    raise ValueError(f"refusing archive link or device {member.name!r}")
            tf.extractall(into, filter="data")
    entries = [e for e in into.iterdir() if e.name != "__MACOSX"]
    return entries[0] if len(entries) == 1 and entries[0].is_dir() else into


def _snapshot_git(folder: Path, dest: Path, base: str) -> None:
    """dest/.git: one commit of `folder` on branch `base`, also at refs/remotes/origin/<base>.

    The folder is read in place through --work-tree and never written to. Its own .gitignore files
    apply, plus SNAPSHOT_EXCLUDES."""
    gitdir = dest / ".git"
    dest.mkdir(parents=True, exist_ok=True)
    _git(["init", "--quiet", f"--initial-branch={base}", str(dest)])
    (gitdir / "info").mkdir(parents=True, exist_ok=True)
    (gitdir / "info" / "exclude").write_text("\n".join(SNAPSHOT_EXCLUDES) + "\n", encoding="utf-8")
    tree = ["-c", "core.autocrlf=false", "-c", "core.safecrlf=false", "-c", "user.name=anvil",
            "-c", "user.email=anvil@localhost", f"--git-dir={gitdir}", f"--work-tree={folder}"]
    _git([*tree, "add", "-A", "."], cwd=folder)
    _git([*tree, "commit", "--quiet", "--allow-empty", "-m", f"Snapshot of {folder.name} for an Anvil run"],
         cwd=folder)
    _git([f"--git-dir={gitdir}", "update-ref", f"refs/remotes/origin/{base}", f"refs/heads/{base}"])


def _tar_gitdir(dest: Path, name: str) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        # Member names built by hand: tarfile's recursive add joins with os.sep, so on Windows the
        # sandbox would receive files literally named ".git\HEAD".
        gitdir = dest / ".git"
        tar.add(dest, arcname=name, recursive=False)  # else Docker creates it root-owned
        tar.add(gitdir, arcname=f"{name}/.git", recursive=False)
        for item in sorted(gitdir.rglob("*")):
            tar.add(item, arcname=f"{name}/.git/{item.relative_to(gitdir).as_posix()}", recursive=False)
    return buf.getvalue()


def local_copy_tar(repo: str, base: str | None = None) -> bytes:
    """A tar of /<name>/.git holding the project's base as a single-branch repo, for put_archive at
    /workspace. Only the git directory travels: the sandbox builds the working tree itself
    (`local_checkout_script`), so file modes and LF endings come from git, not from a Windows checkout.
    Inside, the base commit sits at refs/remotes/origin/<base> and there is no remote to fetch from."""
    repo = validate_local(repo)
    kind, name = local_kind(repo), local_name(repo)
    base = base or default_base(repo)
    with tempfile.TemporaryDirectory(prefix="anvil-src-") as tmp:
        dest = Path(tmp) / name
        if kind == "git":
            ref = _base_ref(repo, base)
            _git(["init", "--quiet", str(dest)])
            _git(["fetch", "--quiet", "--no-tags", f"--depth={LOCAL_DEPTH}", Path(repo).resolve().as_uri(),
                  f"+{ref}:refs/remotes/origin/{base}"], cwd=dest)
            _git(["update-ref", f"refs/heads/{base}", f"refs/remotes/origin/{base}"], cwd=dest)
            _git(["symbolic-ref", "HEAD", f"refs/heads/{base}"], cwd=dest)
        else:
            folder = Path(repo)
            if kind == "archive":
                folder = _extract_archive(folder, Path(tmp) / "_extracted")
            _snapshot_git(folder, dest, base)
        return _tar_gitdir(dest, name)


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
