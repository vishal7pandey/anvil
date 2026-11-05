"""ANVIL-99: host-side verification must not run through the agent's shell.

Every sandbox command used to be `bash -lc`, a login shell that reads ~/.profile and ~/.bashrc, files the
agent owns. A PATH shim or a profile line could make `git` / `python3` report anything, so acceptance checks
and `verify_outcome` were only as honest as the agent's dotfiles. Verification now runs through
`DockerSandbox.run_verified`: empty environment, no shell startup files, absolute tool paths, git configuration
from the environment, and check scripts uploaded root-owned to /opt/anvil.

The first group needs no Docker (argv level). The second runs in the real sandbox image as the real `agent`
user and is skipped when Docker or the image is missing; each test first proves the attack works against the
agent's own shell (the control), so a passing result means something.
"""

import io
import tarfile
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from anvil.runtime.sandbox import (DEFAULT_IMAGE, TRUSTED_DIR, VERIFY_ENV, DockerSandbox, SandboxLimits, SandboxSpec,
                                   upload_trusted, verify_run)


# -- argv level (no Docker) ---------------------------------------------------------------------------------

def _sandbox():
    container = MagicMock()
    container.exec_run.return_value = SimpleNamespace(output=b"ok\n", exit_code=0)
    container.put_archive.return_value = True
    return DockerSandbox(container), container


def test_verification_runs_with_no_login_shell_and_a_clean_environment():
    sb, container = _sandbox()
    res = sb.run_verified("git status", timeout=30)
    argv = container.exec_run.call_args.args[0]
    assert res.output == "ok\n" and res.exit_code == 0
    assert argv[:2] == ["/usr/bin/env", "-i"]                       # nothing inherited from the agent's session
    assert argv[-5:] == ["/bin/bash", "--noprofile", "--norc", "-c", "git status"]
    assert "-l" not in argv and "-lc" not in argv and "bash" not in argv[:-5]   # no login shell, no outer shell
    assert all(item in argv for item in VERIFY_ENV)
    assert "/usr/bin/timeout" in argv                               # the time budget still applies


def test_the_agents_own_commands_keep_the_login_shell():
    sb, container = _sandbox()
    sb.execute("ls")
    assert container.exec_run.call_args.args[0][:2] == ["bash", "-lc"]      # toolchain on PATH, as before
    sb.run_internal("ls")
    assert container.exec_run.call_args.args[0][:2] == ["bash", "-lc"]      # setup commands unchanged too


def test_trusted_upload_only_accepts_the_trusted_dir_and_writes_root_owned_files():
    sb, container = _sandbox()
    results = sb.upload_trusted([
        ("/workspace/.anvil/checks/a.py", b"x"),            # the agent's own tree: refused
        (TRUSTED_DIR + "/../etc/passwd", b"x"),             # traversal: refused
        (TRUSTED_DIR + "/checks/a.py", b"print(1)\n"),
    ])
    assert [r.error for r in results] == ["invalid_path", "invalid_path", None]
    mkdir = container.exec_run.call_args
    assert mkdir.args[0] == ["mkdir", "-p", TRUSTED_DIR + "/checks"] and mkdir.kwargs["user"] == "root"
    parent, data = container.put_archive.call_args.args
    assert parent == TRUSTED_DIR + "/checks"
    member = tarfile.open(fileobj=io.BytesIO(data)).getmembers()[0]
    assert (member.uid, member.gid, member.mode) == (0, 0, 0o644)


def test_helpers_fall_back_for_sandboxes_without_the_hardened_methods():
    class Fake:
        def execute(self, command, timeout=None):
            return SimpleNamespace(output="via execute", exit_code=0)

        def upload_files(self, files):
            return [SimpleNamespace(path=p, error=None) for p, _ in files]

    assert verify_run(Fake(), "git status").output == "via execute"
    assert [r.path for r in upload_trusted(Fake(), [(TRUSTED_DIR + "/x", b"")])] == [TRUSTED_DIR + "/x"]


# -- real sandbox image, real `agent` user ------------------------------------------------------------------

def _image_ok() -> bool:
    try:
        import docker
        docker.from_env().images.get(DEFAULT_IMAGE)
        return True
    except Exception:
        return False


needs_image = pytest.mark.skipif(not _image_ok(), reason=f"Docker or image {DEFAULT_IMAGE} not available")


@pytest.fixture(scope="module")
def box():
    spec = SandboxSpec(name=f"claude-verify-{uuid.uuid4().hex[:8]}", limits=SandboxLimits(cpus=1, memory="512m"),
                       network="none")
    sandbox = DockerSandbox.start(spec)
    yield sandbox
    sandbox.stop()


@needs_image
def test_a_path_shim_and_profile_line_cannot_change_a_verification_result(box):
    # The agent plants a fake python3 and a profile line, in files it owns.
    box.execute("mkdir -p ~/shim && printf '#!/bin/sh\\necho FAKE\\n' > ~/shim/python3 && chmod +x ~/shim/python3")
    box.execute("printf 'export PATH=$HOME/shim:$PATH\\necho HIJACKED\\n' >> ~/.profile")
    probe = "command -v python3; python3 -c 'print(40+2)'"

    control = box.execute(probe)                         # the agent's own shell IS hijacked: the attack is real
    assert "FAKE" in control.output and "HIJACKED" in control.output

    verified = box.run_verified(probe)
    assert verified.exit_code == 0
    assert "42" in verified.output
    assert "FAKE" not in verified.output and "HIJACKED" not in verified.output and "shim" not in verified.output


def _marker(box) -> str:
    """'ran' if the planted fsmonitor program left its marker file, else 'clean' (last line: the agent's profile,
    planted by the test above, prints extra lines first)."""
    return box.execute("test -e /tmp/fsmon-ran && echo ran || echo clean").output.strip().splitlines()[-1]


@needs_image
def test_a_repo_config_cannot_make_git_run_a_program_during_verification(box):
    box.execute("rm -rf /tmp/repo /tmp/fsm.sh /tmp/fsmon-ran && git init -q /tmp/repo && cd /tmp/repo "
                "&& git config user.email a@b && git config user.name a && echo x > f && git add f && git commit -qm init "
                "&& printf '#!/bin/sh\\ntouch /tmp/fsmon-ran\\necho\\n' > /tmp/fsm.sh && chmod +x /tmp/fsm.sh "
                "&& git config core.fsmonitor /tmp/fsm.sh")
    box.execute("cd /tmp/repo && git status")            # control: the agent's own git runs the planted program
    assert _marker(box) == "ran"

    box.execute("rm -f /tmp/fsmon-ran")
    verified = box.run_verified("cd /tmp/repo && git status --porcelain && git rev-parse --abbrev-ref HEAD")
    assert verified.exit_code == 0, verified.output
    assert _marker(box) == "clean"


@needs_image
def test_trusted_files_are_root_owned_and_the_agent_cannot_change_them(box):
    uploaded = box.upload_trusted([(TRUSTED_DIR + "/checks/probe.py", b"print('trusted')\n")])
    assert uploaded[0].error is None
    owner = box.execute("stat -c '%U %a' /opt/anvil/checks/probe.py /opt/anvil/checks").output
    assert "root 644" in owner and "root 755" in owner
    assert "exit=1" in box.execute("echo tampered >> /opt/anvil/checks/probe.py; echo exit=$?").output
    assert "trusted" in box.run_verified("python3 /opt/anvil/checks/probe.py").output
