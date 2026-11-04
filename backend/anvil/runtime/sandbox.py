"""Docker-backed sandbox for Deep Agents: one container per session.

The container gets no credentials and runs as the image's non-root user. Everything the agent
does to files and processes goes through `execute`, `upload_files` and `download_files`; the
rest of the filesystem API (read, write, edit, grep, glob, ls) comes from `BaseSandbox`.
"""

from __future__ import annotations

import io
import os
import posixpath
import re
import shlex
import tarfile
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import docker
from docker.errors import APIError, NotFound
from docker.models.containers import Container

from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import MAX_OUTPUT_BYTES, BaseSandbox

DEFAULT_IMAGE = "anvil-sandbox-rtems:7.1"  # :7 + installed sparc/leon3 BSP (Dockerfile.leon3)
TIMEOUT_EXIT_CODE = 124  # coreutils `timeout`
REFUSED_EXIT_CODE = 126

# Commands that cost a whole run budget for no benefit. TC1 runs 2 and 3 (ANVIL-41) each spent
# 20-30 min in `pre-commit run --all-files` over the RTEMS tree after the fix was already done; the
# skill says `--files`, so refusing with the right command costs the agent one tool call.
_REFUSED = [
    (re.compile(r"\bpre-commit\s+run\b[^;&|\n]*\s(--all-files|-a)(?=\s|$|[;&|)'\"])"),
     "refused: `pre-commit run --all-files` checks the whole repository and takes longer than the run "
     "budget. Call the `lint_changed` tool instead: it runs the hooks on the files you changed."),
]

# What a command's output may put into the model's context (ANVIL-89). Larger output is cut to its
# head + tail, and the complete output is written to SPILL_DIR in the sandbox for grep/tail.
MODEL_OUTPUT_BYTES = int(os.environ.get("ANVIL_MODEL_OUTPUT_BYTES", "16000"))
MODEL_OUTPUT_HEAD = 2000
SPILL_DIR = "/workspace/.anvil/out"


def host_run(sandbox: Any, command: str, *, timeout: int | None = None) -> Any:
    """Run a host-internal command whose output the host parses: complete output when the sandbox
    offers `run_internal` (DockerSandbox), else plain `execute` (test fakes)."""
    runner = getattr(sandbox, "run_internal", None)
    return runner(command, timeout=timeout) if runner else sandbox.execute(command, timeout=timeout)



def refusal(command: str) -> str | None:
    """The reason `command` is refused in the sandbox, or None."""
    for pattern, reason in _REFUSED:
        if pattern.search(command):
            return reason
    return None


def budget_timeout(requested: int, deadline: float | None, now: float) -> int | None:
    """Clamp a command timeout to the run's time budget; None when the budget is already used up."""
    if deadline is None:
        return requested
    remaining = int(deadline - now)
    if remaining <= 0:
        return None
    return remaining if requested == 0 else min(requested, remaining)


@dataclass
class SandboxLimits:
    cpus: float = 4.0
    memory: str = "4g"
    pids: int = 2048


@dataclass
class SandboxSpec:
    image: str = DEFAULT_IMAGE
    name: str | None = None
    workdir: str = "/workspace"
    user: str = "agent"
    uid: int = 1001
    limits: SandboxLimits = field(default_factory=SandboxLimits)
    labels: dict[str, str] = field(default_factory=dict)
    network: str = "bridge"  # needs egress for git clone; no secrets inside, see ADR-004


class DockerSandbox(BaseSandbox):
    """`BaseSandbox` over a long-lived Docker container."""

    enable_capture_offload = True  # bash + coreutils in our images

    def __init__(self, container: Container, *, workdir: str = "/workspace", uid: int = 1001,
                 default_timeout: int = 30 * 60) -> None:
        self._container = container
        self._workdir = workdir
        self._uid = uid
        self._default_timeout = default_timeout
        # time.monotonic() value the run's time budget ends at: set by the executor while the agent
        # runs, cleared for the handoff. Without it one long command overran the 45-min budget by
        # 16 min (ANVIL-41), because the budget was only checked between tool calls.
        self.deadline: float | None = None
        # (command, duration, timed_out) per execute, taken by the executor for sandbox.exec events.
        self.exec_log: list[dict] = []
        # Optional host hook: called with (command, FULL decoded output, exit_code) before the
        # output is cut for the model (ANVIL-84 writes commands/NNNN.log through it).
        self.on_exec: Any = None
        # Set by the executor when the run is cancelled; host tools that wait (CI polling) check it
        # so a cancel does not wait out their sleep (ANVIL-76).
        self.stop_requested = threading.Event()
        self._spill_seq = 0

    # -- lifecycle -------------------------------------------------------------------------

    @classmethod
    def start(cls, spec: SandboxSpec, client: docker.DockerClient | None = None) -> DockerSandbox:
        client = client or docker.from_env()
        # Docker rejects a CPU limit above the host's count (CI runners have 2): cap it there.
        cpus = min(spec.limits.cpus, float(client.info().get("NCPU") or spec.limits.cpus))
        container = client.containers.run(
            spec.image,
            command=["sleep", "infinity"],
            name=spec.name,
            detach=True,
            user=spec.user,
            working_dir=spec.workdir,
            environment={},  # deliberately empty: no tokens in the sandbox
            labels={"anvil.sandbox": "1", **spec.labels},
            nano_cpus=int(cpus * 1e9),
            mem_limit=spec.limits.memory,
            pids_limit=spec.limits.pids,
            network=spec.network,
            security_opt=["no-new-privileges"],
            cap_drop=["ALL"],
        )
        return cls(container, workdir=spec.workdir, uid=spec.uid)

    @classmethod
    def attach(cls, name_or_id: str, client: docker.DockerClient | None = None) -> DockerSandbox:
        client = client or docker.from_env()
        return cls(client.containers.get(name_or_id))

    def stop(self, *, remove: bool = True) -> None:
        try:
            self._container.stop(timeout=5)
            if remove:
                self._container.remove(force=True)
        except NotFound:
            pass

    def set_network(self, network: str | None) -> None:
        """Connect to `network`, or disconnect from every network when None (offline sandbox)."""
        self._container.reload()
        attached = list((self._container.attrs.get("NetworkSettings", {}).get("Networks") or {}).keys())
        client = self._container.client
        for name in attached:
            if name != network:
                client.networks.get(name).disconnect(self._container)
        if network and network not in attached:
            client.networks.get(network).connect(self._container)

    def set_workdir(self, path: str) -> None:
        """Where `execute` runs commands. The executor points it at the run's repo checkout: agents and
        their subagents kept running `./waf` from /workspace (exit 127), ~20 tool calls per TC1
        rehearsal on 2026-11-04 (ANVIL-32)."""
        self._workdir = path

    @property
    def id(self) -> str:
        return self._container.name

    # -- core primitives -------------------------------------------------------------------

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """A command for the agent: its output is bounded for the model's context (ANVIL-89)."""
        return self._run(command, timeout=timeout, for_model=True)

    def run_internal(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """A command the host runs for itself (checkout, diff state, verify, export, push): the complete
        output, never cut or spilled, never shown to the agent. Same refusals and time budget."""
        return self._run(command, timeout=timeout, for_model=False)

    def _run(self, command: str, *, timeout: int | None, for_model: bool) -> ExecuteResponse:
        requested = timeout if timeout is not None else self._default_timeout
        reason = refusal(command)
        if reason:
            return ExecuteResponse(output=f"[anvil] {reason}", exit_code=REFUSED_EXIT_CODE)
        effective = budget_timeout(requested, self.deadline, time.monotonic())
        if effective is None:
            return ExecuteResponse(output="[anvil] refused: the run's time budget is used up",
                                   exit_code=REFUSED_EXIT_CODE)
        wrapped = command if effective == 0 else (
            f"timeout --kill-after=10 {int(effective)} bash -lc {shlex.quote(command)}"
        )
        started = time.monotonic()
        try:
            result = self._container.exec_run(
                ["bash", "-lc", wrapped], workdir=self._workdir, demux=False
            )
        except APIError as exc:
            return ExecuteResponse(output=f"sandbox error: {exc.explanation or exc}", exit_code=None)
        full = result.output or b""
        if self.on_exec is not None:
            try:
                self.on_exec(command, full.decode("utf-8", errors="replace"), result.exit_code)
            except Exception:  # noqa: BLE001 - logging must never break a command result
                pass
        elapsed = time.monotonic() - started
        if not for_model:  # host-internal: the complete output, nothing spilled (ANVIL-89)
            return ExecuteResponse(output=full.decode("utf-8", errors="replace"), exit_code=result.exit_code,
                                   truncated=False)
        output, truncated = self._for_model(full)
        timed_out = bool(result.exit_code == TIMEOUT_EXIT_CODE and effective and elapsed >= effective)
        if timed_out:
            output += f"\n[anvil] command timed out after {effective}s"
            if effective != requested:
                output += " (the run's time budget ended)"
        # Exact timing for the run report (ANVIL-83); the executor attaches it to the sandbox.exec event.
        self.exec_log.append({"cmd": command, "duration_s": round(elapsed, 3), "timed_out": timed_out})
        return ExecuteResponse(output=output, exit_code=result.exit_code, truncated=truncated)

    def _for_model(self, full: bytes) -> tuple[str, bool]:
        """What the model sees of a command's output: all of it when small, else head + tail + a pointer.

        Up to 512 KB per command used to go into the model's context, and every later call re-read
        it: TC1 rehearsal 2's builder subagent took 5.15M input tokens in 197 calls, mostly waf logs
        (ANVIL-89). The complete output is kept in the sandbox so the agent can grep or tail it.
        """
        if len(full) <= MODEL_OUTPUT_BYTES:
            return full.decode("utf-8", errors="replace"), False
        self._spill_seq += 1
        path = f"{SPILL_DIR}/{int(time.time() * 1000)}-{self._spill_seq:04d}.log"  # unique across runs
        kept = self.upload_files([(path, full[-MAX_OUTPUT_BYTES * 8:])])  # complete up to 4 MB
        where = path if kept and not kept[0].error else None
        head = full[:MODEL_OUTPUT_HEAD].decode("utf-8", errors="replace")
        tail = full[-(MODEL_OUTPUT_BYTES - MODEL_OUTPUT_HEAD):].decode("utf-8", errors="replace")
        omitted = len(full) - MODEL_OUTPUT_HEAD - (MODEL_OUTPUT_BYTES - MODEL_OUTPUT_HEAD)
        note = (f"\n[anvil] ... {omitted} bytes of output omitted here"
                + (f"; the complete output is in {where} (use grep, tail or sed on it)" if where else "")
                + " ...\n")
        return head + note + tail, True

    def take_timing(self, command: str) -> dict | None:
        """Pop the oldest recorded timing for `command` (tool calls may run in parallel)."""
        for i, entry in enumerate(self.exec_log):
            if entry["cmd"] == command:
                del self.exec_log[i]
                return {"duration_s": entry["duration_s"], "timed_out": entry["timed_out"]}
        return None

    def upload_tar(self, parent: str, data: bytes) -> None:
        """Extract a tar under `parent`, owned by the sandbox user (ANVIL-55: a local source's .git).

        Entries made on Windows carry uid 0 and no exec bits on directories; re-owned and made
        traversable here so the non-root agent can work in what it receives.
        """
        if not parent.startswith("/"):
            raise ValueError(f"upload_tar: {parent!r} is not absolute")
        mk = self._container.exec_run(["mkdir", "-p", parent])
        if mk.exit_code != 0:
            raise RuntimeError(f"upload_tar: cannot create {parent}")
        out = io.BytesIO()
        with tarfile.open(fileobj=io.BytesIO(data)) as src, tarfile.open(fileobj=out, mode="w") as dst:
            for info in src.getmembers():
                if info.issym() or info.islnk() or info.isdev() or info.name.startswith(("/", "..")) or "/../" in info.name:
                    raise ValueError(f"upload_tar: refusing entry {info.name!r}")
                info.uid = info.gid = self._uid
                info.uname = info.gname = ""
                info.mode = 0o755 if info.isdir() else (info.mode | 0o644) & 0o755
                dst.addfile(info, src.extractfile(info) if info.isfile() else None)
        if not self._container.put_archive(parent, out.getvalue()):
            raise RuntimeError(f"upload_tar: put_archive into {parent} failed")

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        responses: list[FileUploadResponse] = []
        for path, content in files:
            if not path.startswith("/"):
                responses.append(FileUploadResponse(path=path, error="invalid_path"))
                continue
            parent = posixpath.dirname(path)
            mk = self._container.exec_run(["mkdir", "-p", parent])
            if mk.exit_code != 0:
                responses.append(FileUploadResponse(path=path, error="permission_denied"))
                continue
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w") as tar:
                info = tarfile.TarInfo(name=posixpath.basename(path))
                info.size = len(content)
                info.mode = 0o644
                info.uid = info.gid = self._uid
                info.mtime = int(time.time())
                tar.addfile(info, io.BytesIO(content))
            try:
                ok = self._container.put_archive(parent, buf.getvalue())
            except (APIError, NotFound):
                ok = False
            responses.append(FileUploadResponse(path=path, error=None if ok else "permission_denied"))
        return responses

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        responses: list[FileDownloadResponse] = []
        for path in paths:
            try:
                stream, stat = self._container.get_archive(path)
            except NotFound:
                responses.append(FileDownloadResponse(path=path, content=None, error="file_not_found"))
                continue
            except APIError:
                responses.append(FileDownloadResponse(path=path, content=None, error="permission_denied"))
                continue
            if stat.get("mode", 0) & 0x80000000:  # os.ModeDir in Go's FileMode
                responses.append(FileDownloadResponse(path=path, content=None, error="is_directory"))
                continue
            with tarfile.open(fileobj=io.BytesIO(b"".join(stream))) as tar:
                member = tar.next()
                extracted = tar.extractfile(member) if member else None
                data = extracted.read() if extracted else None
            responses.append(FileDownloadResponse(
                path=path, content=data, error=None if data is not None else "file_not_found"))
        return responses
