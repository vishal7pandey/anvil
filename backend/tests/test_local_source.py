"""A run on a local git working copy instead of GitHub (ANVIL-55): the Friday offline path."""

import subprocess
import tarfile
import io
import uuid
from types import SimpleNamespace

import pytest

from anvil.api.schemas import normalize_repository
from anvil.runtime import source
from anvil.runtime.executor import DeepAgentExecutor, repo_path
from anvil.tools.run_scope import run_tools, verify_outcome


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def host_repo(tmp_path):
    """A working copy with the base branch, an executable script and a 'withheld fix' branch."""
    repo = tmp_path / "rtems"
    repo.mkdir()
    git("init", "-q", "-b", "main", cwd=repo)
    git("config", "user.email", "t@t", cwd=repo)
    git("config", "user.name", "t", cwd=repo)
    (repo / "mutexinit.c").write_text("int init(void) { return 0; }\n")
    (repo / "build.sh").write_text("#!/bin/sh\necho built\n")
    git("add", "-A", cwd=repo)
    git("update-index", "--chmod=+x", "build.sh", cwd=repo)
    git("commit", "-qm", "base", cwd=repo)
    git("switch", "-qc", "fix", cwd=repo)
    (repo / "mutexinit.c").write_text("int init(void) { return 1; } /* the withheld fix */\n")
    git("commit", "-qam", "withheld fix", cwd=repo)
    git("switch", "-q", "main", cwd=repo)
    return repo


# -- paths and validation -----------------------------------------------------------------------

@pytest.mark.parametrize("repo", ["C:\\work\\rtems", "C:/work/rtems", "/home/u/rtems", "D:\\x\\rtems-lwip"])
def test_local_paths_are_recognised_and_named_by_folder(repo):
    assert source.is_local(repo)
    assert repo_path(repo) == "/workspace/" + repo.replace("\\", "/").rsplit("/", 1)[1]


@pytest.mark.parametrize("repo", ["vishal7pandey/rtems", "rtems", "github.com/x/y"])
def test_github_names_are_not_local(repo):
    assert not source.is_local(repo)


# Since ANVIL-91 the host path never reaches a shell (subprocess argument lists only; the sandbox
# sees a slug), so spaces and shell characters are fine. Quotes, '..' and a bare drive are not.
@pytest.mark.parametrize("bad", ["C:" + chr(92) + "work" + chr(92) + ".." + chr(92) + "x",
                                 "C:" + chr(92) + "work" + chr(92) + 'a"b', "C:" + chr(92)])
def test_unsafe_local_paths_are_refused(bad):
    with pytest.raises(ValueError):
        source.validate_local(bad, check_exists=False)


def test_api_accepts_an_existing_working_copy_and_refuses_a_missing_one(host_repo, tmp_path):
    assert normalize_repository(str(host_repo)) == str(host_repo)
    with pytest.raises(ValueError, match="does not exist"):
        normalize_repository(str(tmp_path / "nope"))
    assert normalize_repository("vishal7pandey/rtems") == "vishal7pandey/rtems"


# -- the copy the sandbox receives ------------------------------------------------------------

def test_copy_holds_only_the_base_branch_and_only_git(host_repo):
    data = source.local_copy_tar(str(host_repo), "main")
    names = tarfile.open(fileobj=io.BytesIO(data)).getnames()
    assert all(n in ("rtems", "rtems/.git") or n.startswith("rtems/.git/") for n in names)  # no working tree files
    assert not any("refs/heads/fix" in n or "refs/remotes/origin/fix" in n for n in names)


def test_base_falls_back_to_the_remote_tracking_branch(host_repo, tmp_path):
    clone = tmp_path / "clone"
    git("clone", "-q", "--no-local", str(host_repo), str(clone), cwd=tmp_path)
    git("switch", "-qc", "work", cwd=clone)
    git("branch", "-qD", "main", cwd=clone)  # only origin/main remains
    assert source._base_ref(str(clone), "main") == "refs/remotes/origin/main"
    with pytest.raises(RuntimeError, match="branch 'nope' not found"):
        source._base_ref(str(clone), "nope")


# -- tools and verification -------------------------------------------------------------------

def test_a_local_run_gets_no_github_tools(host_repo):
    names = [t.__name__ for t in run_tools({"run_id": "r", "repo": str(host_repo), "base_ref": "main"}, None)]
    assert not any(n.startswith(("git_push", "github_")) for n in names)
    gh = [t.__name__ for t in run_tools({"run_id": "r", "repo": "vishal7pandey/rtems", "base_ref": "main"}, None)]
    assert "git_push" in gh and "github_open_pr" in gh


def test_local_verify_needs_committed_work_and_never_calls_github(host_repo, monkeypatch):
    import anvil.tools.github as github

    monkeypatch.setattr(github, "_gh", lambda *a, **k: pytest.fail("a local run must not call GitHub"))

    class Sb:
        def __init__(self, dirty):
            self.dirty = dirty

        def execute(self, command, timeout=None):
            return SimpleNamespace(output=" M mutexinit.c" if self.dirty else "", exit_code=0)

    run = {"run_id": "r", "repo": str(host_repo), "base_ref": "main"}
    assert verify_outcome(run, Sb(dirty=False)) == []
    assert "uncommitted" in verify_outcome(run, Sb(dirty=True))[0]


# -- integration: real sandbox image, non-root ---------------------------------------------------

docker = pytest.importorskip("docker")


def _image():
    try:
        client = docker.from_env()
        client.ping()
        client.images.get("anvil-sandbox-rtems:7.1")
        return "anvil-sandbox-rtems:7.1"
    except Exception:
        return None


@pytest.mark.skipif(_image() is None, reason="Docker or the sandbox image is not available")
def test_local_run_end_to_end_in_the_sandbox(host_repo, tmp_path, monkeypatch):
    from anvil.runtime.sandbox import DockerSandbox, SandboxSpec

    monkeypatch.setenv("ANVIL_RUNS_DIR", str(tmp_path / "runs"))
    run = {"run_id": f"local-{uuid.uuid4().hex[:8]}", "session_id": f"t-{uuid.uuid4().hex[:8]}",
           "repo": str(host_repo), "base_ref": "main", "jira_key": "RTEMS-1"}
    sb = DockerSandbox.start(SandboxSpec(image=_image(), name=f"claude-{run['session_id']}"))
    try:
        info = DeepAgentExecutor(image=_image()).checkout(sb, run)
        path = info["path"]
        assert path == "/workspace/rtems" and info["network"] == "offline" and len(info["base_sha"]) == 40
        refs = set(sb.execute(f"git -C {path} for-each-ref --format='%(refname)'").output.split())
        assert refs == {"refs/heads/main", "refs/heads/anvil/rtems-1", "refs/remotes/origin/main"}  # no 'fix'
        assert sb.execute(f"git -C {path} status --porcelain").output.strip() == ""  # no mode/CRLF noise
        assert sb.execute(f"test -x {path}/build.sh && {path}/build.sh").output.strip() == "built"
        assert sb.execute(f"cd {path} && echo x > new.txt && git add -A && git commit -qm ok && echo ok").output.strip() == "ok"
        assert sb.execute("getent hosts github.com || echo offline").output.strip() == "offline"

        out = source.export_result(sb, run, path, "anvil/rtems-1")
        assert out["commits"] == 1
        git("am", "-q", out["patch"], cwd=host_repo)  # the person applies it on their working copy
        assert (host_repo / "new.txt").read_text().strip() == "x"
        git("fetch", "-q", out["bundle"], "anvil/rtems-1:from-bundle", cwd=host_repo)
    finally:
        sb.stop()
