"""git_push end to end against local repos: a bare 'GitHub' remote, a sandbox workspace, a host clone dir. No network."""

import subprocess
from pathlib import Path

import pytest
from deepagents.backends.protocol import ExecuteResponse, FileDownloadResponse

from anvil.tools.errors import ToolError
from anvil.tools.git_push import make_git_push, push_from_sandbox


def git(*args, cwd):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class LocalSandbox:
    """Same shape as DockerSandbox.execute/download_files, but runs bash on this machine."""

    def execute(self, command, *, timeout=None):
        r = subprocess.run(["bash", "-c", command], capture_output=True, text=True)
        return ExecuteResponse(output=r.stdout + r.stderr, exit_code=r.returncode)

    def download_files(self, paths):
        return [FileDownloadResponse(path=p, content=Path(p).read_bytes() if Path(p).exists() else None, error=None if Path(p).exists() else "file_not_found") for p in paths]


@pytest.fixture
def setup(tmp_path, monkeypatch):
    remote = tmp_path / "gh" / "vishal7pandey" / "rtems.git"
    remote.mkdir(parents=True)
    git("init", "--bare", "-b", "main", ".", cwd=remote)
    ws = tmp_path / "ws"
    ws.mkdir()
    git("clone", remote.as_uri(), "rtems", cwd=ws)
    repo = ws / "rtems"
    (repo / "a.c").write_text("int a;\n")
    git("add", ".", cwd=repo)
    git("commit", "-m", "base", cwd=repo)
    git("push", "origin", "HEAD:main", cwd=repo)
    git("fetch", cwd=repo)
    monkeypatch.setenv("GITHUB_REMOTE_BASE", (tmp_path / "gh").as_uri())
    monkeypatch.setenv("GITHUB_OWNER", "vishal7pandey")
    monkeypatch.setenv("GITHUB_TOKEN", "fake-gh-token")
    monkeypatch.setenv("ANVIL_REPOS_DIR", str(tmp_path / "host"))
    monkeypatch.setenv("ANVIL_LOCKS_DIR", str(tmp_path / "locks"))
    return remote, ws, repo


def test_push_agent_branch_reaches_remote(setup):
    remote, ws, repo = setup
    git("checkout", "-b", "anvil/rtems-1-fix", cwd=repo)
    (repo / "a.c").write_text("int a = 1;\n")
    git("commit", "-am", "RTEMS-1: Fix", cwd=repo)
    sha = git("rev-parse", "HEAD", cwd=repo)

    out = push_from_sandbox(LocalSandbox(), "rtems", "anvil/rtems-1-fix", workspace=ws.as_posix())
    assert out["sha"] == sha
    assert git("rev-parse", "refs/heads/anvil/rtems-1-fix", cwd=remote) == sha
    assert not (ws / ".anvil-push.bundle").exists()

    (repo / "a.c").write_text("int a = 2;\n")
    git("commit", "-am", "RTEMS-1: Second CI round", cwd=repo)
    push_from_sandbox(LocalSandbox(), "rtems", "anvil/rtems-1-fix", workspace=ws.as_posix())
    assert git("rev-parse", "refs/heads/anvil/rtems-1-fix", cwd=remote) == git("rev-parse", "HEAD", cwd=repo)


def test_refuses_commits_touching_github_dir_on_host_side(setup):
    remote, ws, repo = setup
    git("checkout", "-b", "anvil/rtems-4-sneaky", cwd=repo)
    (repo / "a.c").write_text("int a = 3;\n")
    git("commit", "-am", "RTEMS-4: Fix", cwd=repo)
    (repo / ".github" / "workflows").mkdir(parents=True)
    (repo / ".github" / "workflows" / "ci.yml").write_text("on: push\n")
    git("add", ".", cwd=repo)
    git("commit", "-m", "RTEMS-4: Speed up CI", cwd=repo)
    git("rm", "-r", "-q", ".github", cwd=repo)
    git("commit", "-m", "RTEMS-4: Hide it again", cwd=repo)

    with pytest.raises(ToolError, match=r"protected paths \(.github/workflows/ci.yml\)"):
        push_from_sandbox(LocalSandbox(), "rtems", "anvil/rtems-4-sneaky", workspace=ws.as_posix())
    assert "anvil/rtems-4-sneaky" not in git("branch", "--list", cwd=remote)
    host_clone = next((setup[1].parent / "host").iterdir())
    assert "anvil/rtems-4-sneaky" not in git("branch", "--list", cwd=host_clone)


def test_acquire_lock_writes_owner_and_times_out(tmp_path, monkeypatch):
    from anvil.tools.locks import acquire_lock

    monkeypatch.setenv("ANVIL_LOCKS_DIR", str(tmp_path))
    with acquire_lock("network-heavy", what="clone rtems") as path:
        assert path.read_text().startswith("anvil-runtime ") and "clone rtems" in path.read_text()
        with pytest.raises(ToolError, match="still held"):
            with acquire_lock("network-heavy", timeout=0, poll=0):
                pass
    assert not path.exists()


@pytest.mark.parametrize("branch", ["main", "feature/x", "anvil/../main", "anvil/x;rm -rf /", "anvil/main"])
def test_refuses_unsafe_branches(setup, branch):
    with pytest.raises(ToolError, match="refused"):
        push_from_sandbox(LocalSandbox(), "rtems", branch, workspace=setup[1].as_posix())


def test_nothing_to_push_and_missing_branch(setup):
    _, ws, repo = setup
    git("branch", "anvil/rtems-2-noop", cwd=repo)
    with pytest.raises(ToolError, match="nothing to push"):
        push_from_sandbox(LocalSandbox(), "rtems", "anvil/rtems-2-noop", workspace=ws.as_posix())
    with pytest.raises(ToolError, match="not found"):
        push_from_sandbox(LocalSandbox(), "rtems", "anvil/rtems-3-ghost", workspace=ws.as_posix())


def test_factory_exposes_model_facing_signature():
    tool = make_git_push(LocalSandbox())
    assert tool.__name__ == "git_push" and list(tool.__annotations__) == ["repo", "branch", "return"]
