"""ANVIL-91: point the factory at a local project in any shape (plain folder, any branch, archive,
path with spaces, uncommitted changes reported)."""

import io
import subprocess
import tarfile
import uuid
import zipfile
from pathlib import Path

import pytest

from anvil.api.app import _code_base, _session_base
from anvil.api.schemas import normalize_repository
from anvil.runtime import source


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def make_folder(root: Path) -> Path:
    """A plain project folder (no git) under a path with spaces, with junk that must not travel."""
    folder = root / "My Projects" / "Sensor App"
    (folder / "src").mkdir(parents=True)
    (folder / "src" / "main.c").write_text("int main(void) { return 0; }\n")
    (folder / "README.md").write_text("# app\n")
    (folder / "node_modules" / "x").mkdir(parents=True)
    (folder / "node_modules" / "x" / "big.js").write_text("junk")
    (folder / "build").mkdir()
    (folder / "build" / "out.o").write_text("junk")
    return folder


def unpack_gitdir(data: bytes, into: Path) -> Path:
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        tar.extractall(into, filter="data")
    (repo,) = [p for p in into.iterdir() if p.is_dir()]
    return repo


def tree_of(repo: Path, ref: str = "refs/heads/main") -> set[str]:
    return set(git("ls-tree", "-r", "--name-only", ref, cwd=repo).splitlines())


# -- naming and validation -----------------------------------------------------------------------

@pytest.mark.parametrize("path,slug", [
    (r"C:\Users\First Last\Projects\Sensor App", "Sensor-App"),
    (r"C:\work\rtems", "rtems"),
    (r"D:\drop\firmware v2.zip", "firmware-v2"),
    ("/home/u/my app.tar.gz", "my-app"),
    ('"C:\\work\\app"', "app"),
])
def test_any_folder_name_becomes_a_safe_sandbox_slug(path, slug):
    assert source.local_name(path) == slug


def test_spaces_are_accepted_quotes_and_dotdot_are_not(tmp_path):
    folder = make_folder(tmp_path)
    assert source.validate_local(str(folder)) == str(folder)
    assert normalize_repository(f'"{folder}"') == str(folder)  # pasted with quotes
    for bad in (r'C:\work\a"b', r"C:\work\..\x"):
        with pytest.raises(ValueError):
            source.validate_local(bad, check_exists=False)
    with pytest.raises(ValueError, match="does not exist"):
        source.validate_local(str(tmp_path / "nope"))


# -- shapes ----------------------------------------------------------------------------------------

def test_plain_folder_becomes_a_one_commit_snapshot_without_junk(tmp_path):
    folder = make_folder(tmp_path)
    assert source.local_kind(str(folder)) == "folder" and source.default_base(str(folder)) == "main"
    repo = unpack_gitdir(source.local_copy_tar(str(folder)), tmp_path / "out")
    assert repo.name == "Sensor-App"
    assert tree_of(repo) == {"README.md", "src/main.c"}  # node_modules/ and build/ excluded
    assert git("rev-parse", "refs/remotes/origin/main", cwd=repo) == git("rev-parse", "refs/heads/main", cwd=repo)
    assert not (folder / ".git").exists()  # the folder itself was never written to


def test_folder_gitignore_is_respected(tmp_path):
    folder = make_folder(tmp_path)
    (folder / ".gitignore").write_text("*.log\n")
    (folder / "debug.log").write_text("x")
    repo = unpack_gitdir(source.local_copy_tar(str(folder)), tmp_path / "out")
    assert "debug.log" not in tree_of(repo) and ".gitignore" in tree_of(repo)


@pytest.mark.parametrize("kind", ["zip", "tar.gz"])
def test_archives_are_extracted_and_snapshotted(tmp_path, kind):
    folder = make_folder(tmp_path)
    archive = tmp_path / f"Sensor App.{kind}"
    if kind == "zip":
        with zipfile.ZipFile(archive, "w") as zf:
            for f in folder.rglob("*"):
                zf.write(f, f"Sensor App/{f.relative_to(folder).as_posix()}")
    else:
        with tarfile.open(archive, "w:gz") as tf:
            tf.add(folder, arcname="Sensor App")
    assert source.local_kind(str(archive)) == "archive"
    repo = unpack_gitdir(source.local_copy_tar(str(archive)), tmp_path / "out")
    assert tree_of(repo) == {"README.md", "src/main.c"}  # the single top folder is the project root


def test_zip_slip_is_refused(tmp_path):
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../escape.txt", "x")
    with pytest.raises(ValueError, match="refusing archive entry"):
        source.local_copy_tar(str(archive))


def test_git_repo_on_master_defaults_to_its_current_branch_and_reports_uncommitted(tmp_path):
    repo = tmp_path / "legacy repo"
    repo.mkdir()
    git("init", "-q", "-b", "master", cwd=repo)
    git("config", "user.email", "t@t", cwd=repo)
    git("config", "user.name", "t", cwd=repo)
    (repo / "a.c").write_text("int a;\n")
    git("add", "-A", cwd=repo)
    git("commit", "-qm", "base", cwd=repo)
    (repo / "a.c").write_text("int a = 1;\n")  # uncommitted
    (repo / "new.c").write_text("int n;\n")    # untracked
    assert source.default_base(str(repo)) == "master" and _session_base(str(repo)) == "master"
    assert source.uncommitted_changes(str(repo)) == 2
    out = unpack_gitdir(source.local_copy_tar(str(repo)), tmp_path / "out")
    assert tree_of(out, "refs/heads/master") == {"a.c"}  # the committed base, not the working tree


def test_github_sessions_still_default_to_main():
    assert _session_base("vishal7pandey/rtems") == "main"


# -- the ticket line -------------------------------------------------------------------------------

@pytest.mark.parametrize("line,expected", [
    ("Code base: branch `tc1/5776-base` of C:\\work\\rtems .", ("C:\\work\\rtems", "tc1/5776-base")),
    ('Code base: branch main of "C:\\My Projects\\Sensor App".', ("C:\\My Projects\\Sensor App", "main")),
    ('Code base: "C:\\My Projects\\Sensor App"', ("C:\\My Projects\\Sensor App", None)),
    ("Code base: /home/u/app", ("/home/u/app", None)),
    ("Code base: branch `b` of github.com/o/r", ("o/r", "b")),
])
def test_code_base_line_accepts_quoted_paths_and_an_optional_branch(line, expected):
    assert _code_base(f"Fix it.\n\n{line}\n") == expected


# -- end to end in the real sandbox ------------------------------------------------------------------

docker = pytest.importorskip("docker")


def _image():
    try:
        c = docker.from_env()
        c.ping()
        c.images.get("anvil-sandbox-rtems:7.1")
        return "anvil-sandbox-rtems:7.1"
    except Exception:
        return None


@pytest.mark.skipif(_image() is None, reason="Docker or the sandbox image is not available")
def test_plain_folder_with_spaces_end_to_end(tmp_path, monkeypatch):
    from anvil.runtime.executor import DeepAgentExecutor
    from anvil.runtime.sandbox import DockerSandbox, SandboxSpec

    monkeypatch.setenv("ANVIL_RUNS_DIR", str(tmp_path / "runs"))
    folder = make_folder(tmp_path)
    run = {"run_id": f"shape-{uuid.uuid4().hex[:8]}", "session_id": f"t-{uuid.uuid4().hex[:8]}",
           "repo": str(folder), "jira_key": "APP-1"}  # no base_ref: snapshot default 'main'
    sb = DockerSandbox.start(SandboxSpec(image=_image(), name=f"claude-shape-{uuid.uuid4().hex[:8]}"))
    try:
        info = DeepAgentExecutor(image=_image()).checkout(sb, run)
        assert info["path"] == "/workspace/Sensor-App" and info["source_kind"] == "folder"
        assert info["base_ref"] == "main" and run["base_ref"] == "main"
        files = sb.execute(f"cd {info['path']} && git ls-files").output.split()
        assert sorted(files) == ["README.md", "src/main.c"]
        sb.execute(f"cd {info['path']} && sed -i 's/return 0/return 42/' src/main.c && git commit -qam 'APP-1: fix'")
        out = source.export_result(sb, run, info["path"], info["branch"])
        assert out["commits"] == 1
        # The person applies the patch to the plain folder they pointed us at.
        subprocess.run(["git", "apply", out["patch"]], cwd=folder, check=True)
        assert "return 42" in (folder / "src" / "main.c").read_text()
    finally:
        sb.stop()
