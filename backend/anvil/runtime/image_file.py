"""Sandbox Docker image file archiving and verification (ANVIL-57).

Handles exporting, importing, SHA-256 integrity verification, and metadata
manifests for offline sandbox image archives (e.g. anvil-sandbox-rtems:7.1).
"""

from __future__ import annotations

import hashlib
import json
import os
import tarfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EXPECTED_SANDBOX_TAG = "anvil-sandbox-rtems:7.1"
EXPECTED_ID_PREFIX = "bc17ec29"


@dataclass
class ImageManifest:
    image_tag: str
    image_id: str
    file_name: str
    file_size_bytes: int
    sha256: str
    exported_at: str
    format_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ImageManifest:
        return cls(
            image_tag=data.get("image_tag", ""),
            image_id=data.get("image_id", ""),
            file_name=data.get("file_name", ""),
            file_size_bytes=int(data.get("file_size_bytes", 0)),
            sha256=data.get("sha256", ""),
            exported_at=data.get("exported_at", ""),
            format_version=int(data.get("format_version", 1)),
        )


def compute_sha256(path: Path | str, chunk_size: int = 1024 * 1024) -> str:
    """Compute the hexadecimal SHA-256 digest of a file in chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return h.hexdigest().lower()


def find_checksum_sidecar(file_path: Path | str) -> str | None:
    """Find and extract expected SHA-256 digest from sidecar files (.sha256 or .manifest.json)."""
    p = Path(file_path)
    
    # 1. Check <filename>.sha256
    sha_file = p.with_name(f"{p.name}.sha256")
    if sha_file.exists():
        content = sha_file.read_text(encoding="utf-8").strip()
        # Parse '<hash>' or '<hash> *<filename>' or '<hash>  <filename>'
        parts = content.split()
        if parts and len(parts[0]) == 64:
            return parts[0].lower()

    # 2. Check <filename>.manifest.json
    manifest_file = p.with_name(f"{p.name}.manifest.json")
    if manifest_file.exists():
        try:
            data = json.loads(manifest_file.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("sha256"):
                return str(data["sha256"]).strip().lower()
        except (json.JSONDecodeError, OSError):
            pass

    return None


def read_manifest(manifest_path: Path | str) -> ImageManifest:
    """Read and validate an ImageManifest JSON file."""
    p = Path(manifest_path)
    data = json.loads(p.read_text(encoding="utf-8"))
    return ImageManifest.from_dict(data)


def write_manifest(manifest_path: Path | str, manifest: ImageManifest) -> None:
    """Write an ImageManifest to a JSON file."""
    p = Path(manifest_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(manifest.to_dict(), indent=2) + "\n", encoding="utf-8")


def write_sha256_file(sha_path: Path | str, digest: str, file_name: str) -> None:
    """Write standard format sha256 checksum file."""
    p = Path(sha_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"{digest.lower()} *{file_name}\n", encoding="utf-8")


def verify_image_file(
    image_path: Path | str,
    expected_sha256: str | None = None,
) -> tuple[bool, str]:
    """Verify integrity of an exported image archive against expected SHA-256 digest.
    
    Returns:
        (True, digest) if valid.
        (False, error_message) if invalid or missing.
    """
    p = Path(image_path)
    if not p.is_file():
        return False, f"Image file not found: {p}"

    expected = (expected_sha256 or find_checksum_sidecar(p) or "").strip().lower()
    if not expected:
        return False, f"No expected SHA-256 digest provided or found in sidecars (.sha256, .manifest.json) for {p.name}"

    actual = compute_sha256(p)
    if actual != expected:
        return (
            False,
            f"SHA-256 mismatch for {p.name}: expected {expected}, got {actual}",
        )

    return True, actual


def inspect_docker_tar(tar_path: Path | str) -> dict[str, Any]:
    """Inspect the internal manifest.json inside a docker save tarball.
    
    Returns metadata dict containing tags and config layer paths.
    """
    p = Path(tar_path)
    if not p.is_file():
        raise FileNotFoundError(f"Tar file not found: {p}")

    with tarfile.open(p, "r:*") as tar:
        try:
            member = tar.getmember("manifest.json")
            f = tar.extractfile(member)
            if f is None:
                raise ValueError("manifest.json inside tarball could not be read")
            content = json.loads(f.read().decode("utf-8"))
            if isinstance(content, list) and content:
                return content[0]
            return {}
        except KeyError:
            return {}

