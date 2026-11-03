"""Docker-backed sandbox for Deep Agents: one container per session.

The container gets no credentials and runs as the image's non-root user. Everything the agent
does to files and processes goes through `execute`, `upload_files` and `download_files`; the
rest of the filesystem API (read, write, edit, grep, glob, ls) comes from `BaseSandbox`.
"""

from __future__ import annotations

import io
import posixpath
import shlex
import tarfile
import time
from dataclasses import dataclass, field

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

    @property
    def id(self) -> str:
        return self._container.name

    # -- core primitives -------------------------------------------------------------------

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        effective = timeout if timeout is not None else self._default_timeout
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
        raw = result.output or b""
        truncated = len(raw) > MAX_OUTPUT_BYTES
        if truncated:
            raw = raw[-MAX_OUTPUT_BYTES:]  # keep the tail: build errors are at the end
        output = raw.decode("utf-8", errors="replace")
        if result.exit_code == TIMEOUT_EXIT_CODE and effective and time.monotonic() - started >= effective:
            output += f"\n[anvil] command timed out after {effective}s"
        return ExecuteResponse(output=output, exit_code=result.exit_code, truncated=truncated)

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
