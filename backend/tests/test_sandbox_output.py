"""ANVIL-89: a command's output enters the model's context bounded; the complete output stays on disk."""

import uuid
from types import SimpleNamespace

import pytest

from anvil.runtime import sandbox as sbmod
from anvil.runtime.sandbox import MODEL_OUTPUT_BYTES, DockerSandbox


def bare_sandbox(uploads):
    sb = DockerSandbox.__new__(DockerSandbox)
    sb.exec_log, sb.on_exec, sb._spill_seq = [], None, 0
    sb.upload_files = lambda files: uploads.extend(files) or [SimpleNamespace(path=p, error=None) for p, _ in files]
    return sb


def test_small_output_passes_through_untouched():
    uploads = []
    out, truncated = bare_sandbox(uploads)._for_model(b"ok\n")
    assert (out, truncated, uploads) == ("ok\n", False, [])


def test_large_output_is_head_plus_tail_with_a_pointer_to_the_complete_file():
    uploads = []
    full = b"HEAD-LINE\n" + b"x" * 400_000 + b"\nerror: the real failure is at the end\n"
    out, truncated = bare_sandbox(uploads)._for_model(full)
    assert truncated and len(out.encode()) < MODEL_OUTPUT_BYTES + 400
    assert out.startswith("HEAD-LINE") and out.rstrip().endswith("error: the real failure is at the end")
    path, content = uploads[0]
    assert path.startswith(sbmod.SPILL_DIR + "/") and path in out and content == full


# -- real sandbox ---------------------------------------------------------------------------------

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
def test_real_command_output_is_bounded_and_greppable():
    from anvil.runtime.sandbox import SandboxSpec

    sb = DockerSandbox.start(SandboxSpec(image=_image(), name=f"claude-out-{uuid.uuid4().hex[:8]}"))
    seen = {}
    sb.on_exec = lambda cmd, full, code: seen.update(n=len(full), code=code)
    try:
        res = sb.execute("seq 1 100000; echo FAILED: test psx05", timeout=120)
        assert res.exit_code == 0 and res.truncated
        assert len(res.output.encode()) < MODEL_OUTPUT_BYTES + 400
        assert res.output.rstrip().endswith("FAILED: test psx05")
        assert seen["n"] > 500_000 and seen["code"] == 0  # the hook got the complete output
        path = next(w for w in res.output.split() if w.startswith(sbmod.SPILL_DIR))
        assert sb.execute(f"grep -c '' {path}").output.strip() == "100001"  # all lines kept
    finally:
        sb.stop()
