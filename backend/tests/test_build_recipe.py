"""ANVIL-90: a case's [build] section lands in the agent's task; no section, no change."""

import pytest

from anvil.cases import CaseError, get_case, load_case
from anvil.runtime.executor import _task_message

CHECKOUT = {"path": "/workspace/rtems", "branch": "anvil/rtems-1-fix",
            "base_ref": "tc1/5776-base", "base_sha": "abc123"}


def test_5776_declares_and_injects_the_recipe():
    run = {"jira_key": "RTEMS-1", "repo": "vishal7pandey/rtems"}
    task = _task_message(run, "fix the ticket", CHECKOUT)
    assert "build and test commands for this code base" in task
    assert "./waf configure" in task and "erc32-sis" in task
    case = get_case("rtems-5776")
    assert case.build["configure"].startswith("cat > config.ini")
    # The recipe must not name the withheld fix's test: a <test>.exe placeholder is safe to inject.
    assert "<test>.exe" in case.build["test_one"] and "psx05" not in case.build["test_one"]
    assert "psxtests/" in case.build["test_suite"]


def test_a_case_without_build_changes_nothing(tmp_path):
    # rtems-5806 has no [build]: the task must be byte-for-byte the pre-ANVIL-90 text.
    run = {"jira_key": "RTEMS-2", "repo": "vishal7pandey/rtems"}
    task = _task_message(run, "fix the ticket", CHECKOUT)
    assert "build and test commands" not in task


def test_bad_build_table_is_rejected(tmp_path):
    bad = tmp_path / "x.toml"
    bad.write_text(
        'id = "x"\nrepo = "o/r"\nas_of = "2026-01-01"\n'
        '[base]\nbranch = "b"\n'
        '[ticket]\nproject = "P"\nsummary = "s"\nbody = "t"\n'
        '[upstream]\nfix_commits = ["abc"]\n'
        '[build]\nconfigure = 1\n', encoding="utf-8")
    with pytest.raises(CaseError):
        load_case(bad)


# -- real-image proof (review on #90): the declared recipe runs on tc1/5776-base -----------------

def git(*args, cwd):
    import subprocess
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _image():
    try:
        import docker
        c = docker.from_env()
        c.ping()
        c.images.get("anvil-sandbox-rtems:7.1")
        return "anvil-sandbox-rtems:7.1"
    except Exception:
        return None


def _bundle():
    """The tc1/5776-base bundle for the proof run: env override, else the repo's private/handover
    (worktrees don't share it, so also check the main checkout on this laptop)."""
    import os
    from pathlib import Path

    candidates = [
        os.environ.get("ANVIL_TC1_BUNDLE", ""),
        str(Path(__file__).resolve().parents[2] / "private" / "handover" / "rtems-tc1-5776-base.bundle"),
        r"C:\Dev\projects\anvil\private\handover\rtems-tc1-5776-base.bundle",
    ]
    for c in candidates:
        if c and Path(c).exists():
            return Path(c)
    return None


@pytest.mark.skipif(_image() is None or _bundle() is None,
                    reason="Docker/7.1 image or the tc1/5776-base handover bundle is not available")
def test_the_declared_recipe_runs_in_the_image(tmp_path):
    """Runs case.build's configure/build/test_one on the real tc1/5776-base, no LLM."""
    import uuid

    import docker as _docker
    from anvil.runtime import source
    from anvil.runtime.sandbox import DockerSandbox, SandboxSpec

    case = get_case("rtems-5776")
    repo = tmp_path / "rtems"
    git("clone", "-q", "--branch", "tc1/5776-base", str(_bundle()), str(repo), cwd=tmp_path)
    # A real RTEMS build: the shared laptop allows exactly one at a time.
    from anvil.tools.locks import acquire_lock

    lock = acquire_lock("rtems-build", what="ANVIL-90 recipe proof", timeout=1800, poll=15)
    lock.__enter__()
    sb = DockerSandbox.start(SandboxSpec(image=_image(), name=f"devin-recipe-{uuid.uuid4().hex[:8]}"))
    try:
        sb.set_network(None)
        sb.upload_tar("/workspace", source.local_copy_tar(str(repo), "tc1/5776-base"))
        res = sb.execute(source.local_checkout_script("/workspace/rtems", "tc1/5776-base", "anvil/recipe"),
                         timeout=900)
        assert res.exit_code == 0, res.output[-2000:]
        for step in ("configure", "build", "test_one"):
            cmd = case.build[step].replace("<test>", "psx05")  # host-side: fill the placeholder
            out = sb.execute(f"cd /workspace/rtems && {cmd}", timeout=1800)
            assert out.exit_code == 0, f"{step} failed:\n{out.output[-2000:]}"
    finally:
        sb.stop()
        lock.__exit__(None, None, None)
