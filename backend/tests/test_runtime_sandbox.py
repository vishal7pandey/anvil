"""Integration tests against a real container. Skipped when Docker is not reachable."""

import uuid

import pytest

docker = pytest.importorskip("docker")

from anvil.runtime.sandbox import DockerSandbox, SandboxLimits, SandboxSpec  # noqa: E402


def _docker_ok() -> bool:
    try:
        docker.from_env().ping()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _docker_ok(), reason="Docker not reachable")


@pytest.fixture(scope="module")
def sandbox():
    # Small image with bash + python3 (BaseSandbox runs python3 inside the sandbox for read/edit),
    # so the test does not depend on the 1-2 h RTEMS image build.
    spec = SandboxSpec(image="python:3.12-slim", name=f"claude-test-{uuid.uuid4().hex[:8]}", user="root",
                       uid=0, limits=SandboxLimits(cpus=1, memory="256m"), network="none")
    sb = DockerSandbox.start(spec)
    yield sb
    sb.stop()


def test_execute_output_and_exit_code(sandbox):
    r = sandbox.execute("echo hello && pwd && exit 3")
    assert r.exit_code == 3 and "hello" in r.output and "/workspace" in r.output


def test_no_secrets_in_environment(sandbox):
    r = sandbox.execute("env")
    for name in ("OPENAI_API_KEY", "GITHUB_TOKEN", "ATLASSIAN_API_TOKEN", "LITELLM_MASTER_KEY"):
        assert name not in r.output


def test_timeout(sandbox):
    r = sandbox.execute("sleep 5", timeout=1)
    assert r.exit_code == 124 and "timed out" in r.output


def test_upload_download_roundtrip_and_errors(sandbox):
    up = sandbox.upload_files([("/workspace/a/b/c.txt", b"data\n"), ("relative.txt", b"x")])
    assert up[0].error is None and up[1].error == "invalid_path"
    down = sandbox.download_files(["/workspace/a/b/c.txt", "/workspace/missing", "/workspace/a"])
    assert down[0].content == b"data\n"
    assert down[1].error == "file_not_found"
    assert down[2].error == "is_directory"


def test_base_sandbox_file_tools_work_on_top(sandbox):
    sandbox.write("/workspace/src/x.c", "int x = 1;\n")
    assert "int x = 1;" in str(sandbox.read("/workspace/src/x.c"))
    assert "x.c" in str(sandbox.glob("**/*.c", path="/workspace"))
