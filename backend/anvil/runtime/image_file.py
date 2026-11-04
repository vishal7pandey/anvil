"""Sandbox Docker image file archiving and verification (ANVIL-57).

Handles exporting, importing, SHA-256 integrity verification, and metadata
manifests for offline sandbox image archives (e.g. anvil-sandbox-rtems:7.1).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
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
    
    Fails closed: returns (False, msg) if no checksum is found or if checksum mismatches.
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
    """Inspect the internal manifest.json inside a docker save tarball."""
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


def export_image(image: str, output_path: Path | str, force: bool = False) -> ImageManifest:
    """Export a Docker image to an archive with SHA-256 and manifest sidecars."""
    p = Path(output_path)
    p.parent.mkdir(parents=True, exist_ok=True)

    if p.exists() and not force:
        raise FileExistsError(f"Output file already exists: {p}. Use force=True to overwrite.")

    # 1. Verify image exists in Docker daemon
    inspect_res = subprocess.run(
        ["docker", "inspect", "--format={{.Id}}", image],
        capture_output=True,
        text=True,
    )
    if inspect_res.returncode != 0 or not inspect_res.stdout.strip():
        raise RuntimeError(f"Docker image '{image}' not found locally: {inspect_res.stderr.strip()}")

    image_id = inspect_res.stdout.strip()

    # 2. docker save
    save_res = subprocess.run(
        ["docker", "save", image, "-o", str(p)],
        capture_output=True,
        text=True,
    )
    if save_res.returncode != 0:
        raise RuntimeError(f"docker save failed: {save_res.stderr.strip()}")

    # 3. Compute SHA-256 and write sidecars
    digest = compute_sha256(p)
    write_sha256_file(p.with_name(f"{p.name}.sha256"), digest, p.name)

    manifest = ImageManifest(
        image_tag=image,
        image_id=image_id,
        file_name=p.name,
        file_size_bytes=p.stat().st_size,
        sha256=digest,
        exported_at=datetime.now(timezone.utc).isoformat(),
    )
    write_manifest(p.with_name(f"{p.name}.manifest.json"), manifest)
    return manifest


def import_image(
    image_path: Path | str,
    expected_tag: str = EXPECTED_SANDBOX_TAG,
    skip_verify: bool = False,
    expected_hash: str | None = None,
) -> tuple[str, str]:
    """Import a Docker image archive into Docker daemon after validating SHA-256 integrity.
    
    Fails closed if checksum is missing and skip_verify=False.
    Validates image ID against manifest.json sidecar if present.
    Returns (loaded_tag, loaded_image_id).
    """
    p = Path(image_path)
    if not p.is_file():
        raise FileNotFoundError(f"Image archive not found: {p}")

    # 1. SHA-256 Verification (Fail closed)
    if not skip_verify:
        ok, msg = verify_image_file(p, expected_sha256=expected_hash)
        if not ok:
            raise ValueError(f"Integrity check failed: {msg}")

    # 2. Check for manifest sidecar to know exact expected image ID
    manifest_sidecar = p.with_name(f"{p.name}.manifest.json")
    expected_manifest = read_manifest(manifest_sidecar) if manifest_sidecar.exists() else None

    # 3. Load via docker load
    load_res = subprocess.run(
        ["docker", "load", "-i", str(p)],
        capture_output=True,
        text=True,
    )
    if load_res.returncode != 0:
        raise RuntimeError(f"docker load failed: {load_res.stderr.strip()}")

    load_out = load_res.stdout.strip()
    
    # 4. Parse loaded tag from docker load output: "Loaded image: <tag>" or "Loaded image ID: <id>"
    loaded_tag = None
    loaded_id = None
    tag_match = re.search(r"Loaded image:\s*([^\r\n]+)", load_out)
    if tag_match:
        loaded_tag = tag_match.group(1).strip()
    else:
        id_match = re.search(r"Loaded image ID:\s*([^\r\n]+)", load_out)
        if id_match:
            loaded_id = id_match.group(1).strip()

    # 5. Inspect Docker daemon to resolve exact Image ID
    inspect_target = loaded_tag or loaded_id or expected_tag
    inspect_res = subprocess.run(
        ["docker", "inspect", "--format={{.Id}}", inspect_target],
        capture_output=True,
        text=True,
    )
    if inspect_res.returncode == 0:
        actual_id = inspect_res.stdout.strip()
    else:
        actual_id = loaded_id

    if not actual_id:
        raise RuntimeError(f"Could not resolve image ID for imported archive from docker load output: {load_out}")

    # 6. Validate image ID against export manifest (exact equality)
    if expected_manifest and expected_manifest.image_id:
        if actual_id != expected_manifest.image_id:
            raise ValueError(
                f"Image ID mismatch against export manifest: expected {expected_manifest.image_id}, got {actual_id}"
            )

    # 7. Apply required tag if not already set
    if expected_tag and loaded_tag != expected_tag:
        tag_res = subprocess.run(["docker", "tag", actual_id, expected_tag], capture_output=True, text=True)
        if tag_res.returncode != 0:
            raise RuntimeError(f"docker tag failed: {tag_res.stderr.strip()}")
        loaded_tag = expected_tag

    return loaded_tag, actual_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Anvil Sandbox Docker Image File Tool (ANVIL-57)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # export
    exp = subparsers.add_parser("export", help="Export Docker image to tar archive with SHA-256 and manifest")
    exp.add_argument("--image", default=EXPECTED_SANDBOX_TAG, help=f"Docker image tag (default: {EXPECTED_SANDBOX_TAG})")
    exp.add_argument("--output", required=True, help="Destination archive path (.tar)")
    exp.add_argument("--force", action="store_true", help="Overwrite existing output file")

    # import
    imp = subparsers.add_parser("import", help="Import Docker image from archive with integrity verification")
    imp.add_argument("--archive", required=True, help="Path to image archive (.tar)")
    imp.add_argument("--tag", default=EXPECTED_SANDBOX_TAG, help=f"Target Docker tag (default: {EXPECTED_SANDBOX_TAG})")
    imp.add_argument("--skip-verify", action="store_true", help="Skip SHA-256 integrity check")
    imp.add_argument("--expected-hash", help="Explicit expected SHA-256 hash (overrides sidecar)")

    # verify
    ver = subparsers.add_parser("verify", help="Verify SHA-256 checksum of an image archive")
    ver.add_argument("--archive", required=True, help="Path to image archive (.tar)")
    ver.add_argument("--expected-hash", help="Explicit expected SHA-256 hash")

    args = parser.parse_args(argv)

    try:
        if args.command == "export":
            manifest = export_image(args.image, args.output, force=args.force)
            print(f"[PASS] Exported {manifest.image_tag} ({manifest.image_id[:19]}...) -> {manifest.file_name}")
            print(f"SHA-256: {manifest.sha256}")
            print(f"Size   : {manifest.file_size_bytes} bytes")
            return 0
        elif args.command == "import":
            tag, img_id = import_image(
                args.archive,
                expected_tag=args.tag,
                skip_verify=args.skip_verify,
                expected_hash=args.expected_hash,
            )
            print(f"[PASS] Imported sandbox image: {tag} ({img_id[:19]}...)")
            return 0
        elif args.command == "verify":
            ok, msg = verify_image_file(args.archive, expected_sha256=args.expected_hash)
            if not ok:
                print(f"[FAIL] {msg}", file=sys.stderr)
                return 1
            print(f"[PASS] SHA-256 verified: {msg}")
            return 0
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
