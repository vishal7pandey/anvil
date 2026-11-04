"""Tests for sandbox Docker image file archiving and verification (ANVIL-57)."""

import hashlib
import json
import tarfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from anvil.runtime.image_file import (
    EXPECTED_ID_PREFIX,
    EXPECTED_SANDBOX_TAG,
    ImageManifest,
    compute_sha256,
    export_image,
    find_checksum_sidecar,
    import_image,
    inspect_docker_tar,
    main,
    read_manifest,
    verify_image_file,
    write_manifest,
    write_sha256_file,
)


def test_compute_sha256(tmp_path: Path):
    test_file = tmp_path / "test.tar"
    content = b"anvil sandbox dummy image content 123456"
    test_file.write_bytes(content)

    expected = hashlib.sha256(content).hexdigest()
    assert compute_sha256(test_file) == expected


def test_manifest_roundtrip(tmp_path: Path):
    manifest_file = tmp_path / "image.tar.manifest.json"
    manifest = ImageManifest(
        image_tag=EXPECTED_SANDBOX_TAG,
        image_id=f"sha256:{EXPECTED_ID_PREFIX}1234567890abcdef",
        file_name="image.tar",
        file_size_bytes=1048576,
        sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        exported_at="2026-11-04T20:00:00Z",
    )

    write_manifest(manifest_file, manifest)
    assert manifest_file.exists()

    loaded = read_manifest(manifest_file)
    assert loaded.image_tag == EXPECTED_SANDBOX_TAG
    assert loaded.image_id.startswith(f"sha256:{EXPECTED_ID_PREFIX}")
    assert loaded.file_size_bytes == 1048576
    assert loaded.sha256 == manifest.sha256


def test_find_checksum_sidecar_sha256_file(tmp_path: Path):
    img_file = tmp_path / "sandbox.tar"
    img_file.write_bytes(b"content")
    digest = hashlib.sha256(b"content").hexdigest()

    # Test format with '*filename'
    write_sha256_file(tmp_path / "sandbox.tar.sha256", digest, "sandbox.tar")
    assert find_checksum_sidecar(img_file) == digest

    # Test plain digest format
    (tmp_path / "sandbox.tar.sha256").write_text(digest + "\n")
    assert find_checksum_sidecar(img_file) == digest


def test_find_checksum_sidecar_manifest(tmp_path: Path):
    img_file = tmp_path / "sandbox.tar"
    img_file.write_bytes(b"content")
    digest = hashlib.sha256(b"content").hexdigest()

    manifest_file = tmp_path / "sandbox.tar.manifest.json"
    manifest_file.write_text(json.dumps({"sha256": digest}))

    assert find_checksum_sidecar(img_file) == digest


def test_verify_image_file_success(tmp_path: Path):
    img_file = tmp_path / "anvil-sandbox-rtems-7.1.tar"
    content = b"real-like tarball payload"
    img_file.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()

    write_sha256_file(tmp_path / "anvil-sandbox-rtems-7.1.tar.sha256", digest, img_file.name)

    ok, msg = verify_image_file(img_file)
    assert ok is True
    assert msg == digest


def test_verify_image_file_mismatch_fails_closed(tmp_path: Path):
    img_file = tmp_path / "anvil-sandbox-rtems-7.1.tar"
    img_file.write_bytes(b"corrupted or tampered content")

    wrong_digest = "0000000000000000000000000000000000000000000000000000000000000000"
    write_sha256_file(tmp_path / "anvil-sandbox-rtems-7.1.tar.sha256", wrong_digest, img_file.name)

    ok, msg = verify_image_file(img_file)
    assert ok is False
    assert "SHA-256 mismatch" in msg


def test_verify_image_file_missing_sidecar_fails_closed(tmp_path: Path):
    img_file = tmp_path / "sandbox.tar"
    img_file.write_bytes(b"data")

    # Fails closed when no sidecar found
    ok, msg = verify_image_file(img_file)
    assert ok is False
    assert "No expected SHA-256 digest provided or found" in msg


def test_verify_image_file_missing_file(tmp_path: Path):
    img_file = tmp_path / "does_not_exist.tar"

    ok, msg = verify_image_file(img_file)
    assert ok is False
    assert "not found" in msg


def test_inspect_docker_tar(tmp_path: Path):
    tar_path = tmp_path / "docker_image.tar"
    docker_manifest = [{"Config": "config.json", "RepoTags": [EXPECTED_SANDBOX_TAG], "Layers": ["layer1.tar"]}]

    manifest_bytes = json.dumps(docker_manifest).encode("utf-8")
    with tarfile.open(tar_path, "w") as tar:
        ti = tarfile.TarInfo("manifest.json")
        ti.size = len(manifest_bytes)
        import io

        tar.addfile(ti, io.BytesIO(manifest_bytes))

    info = inspect_docker_tar(tar_path)
    assert info.get("RepoTags") == [EXPECTED_SANDBOX_TAG]


@patch("subprocess.run")
def test_export_image(mock_run, tmp_path: Path):
    mock_run.side_effect = [
        MagicMock(returncode=0, stdout="sha256:bc17ec29fakeid\n", stderr=""),
        MagicMock(returncode=0, stdout="", stderr=""),
    ]
    out_file = tmp_path / "exported.tar"
    out_file.write_bytes(b"tar-data")

    manifest = export_image(EXPECTED_SANDBOX_TAG, out_file, force=True)
    assert manifest.image_tag == EXPECTED_SANDBOX_TAG
    assert manifest.image_id == "sha256:bc17ec29fakeid"
    assert (tmp_path / "exported.tar.sha256").exists()
    assert (tmp_path / "exported.tar.manifest.json").exists()


@patch("subprocess.run")
def test_import_image_success(mock_run, tmp_path: Path):
    mock_run.side_effect = [
        MagicMock(returncode=0, stdout="Loaded image: anvil-sandbox-rtems:7.1\n", stderr=""),
        MagicMock(returncode=0, stdout="sha256:bc17ec29loaded\n", stderr=""),
    ]
    img_file = tmp_path / "anvil-sandbox-rtems-7.1.tar"
    content = b"image-payload"
    img_file.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    write_sha256_file(tmp_path / "anvil-sandbox-rtems-7.1.tar.sha256", digest, img_file.name)

    tag, img_id = import_image(img_file)
    assert tag == EXPECTED_SANDBOX_TAG
    assert img_id == "sha256:bc17ec29loaded"


def test_import_image_missing_checksum_fails_closed(tmp_path: Path):
    img_file = tmp_path / "unverified.tar"
    img_file.write_bytes(b"content")

    with pytest.raises(ValueError, match="Integrity check failed"):
        import_image(img_file)


@patch("subprocess.run")
def test_import_image_manifest_id_mismatch_fails_closed(mock_run, tmp_path: Path):
    mock_run.side_effect = [
        MagicMock(returncode=0, stdout="Loaded image: anvil-sandbox-rtems:7.1\n", stderr=""),
        MagicMock(returncode=0, stdout="sha256:differentid\n", stderr=""),
    ]
    img_file = tmp_path / "image.tar"
    content = b"content"
    img_file.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    write_sha256_file(tmp_path / "image.tar.sha256", digest, img_file.name)
    write_manifest(
        tmp_path / "image.tar.manifest.json",
        ImageManifest(
            image_tag=EXPECTED_SANDBOX_TAG,
            image_id="sha256:expectedid",
            file_name=img_file.name,
            file_size_bytes=len(content),
            sha256=digest,
            exported_at="2026-11-04T20:00:00Z",
        ),
    )

    with pytest.raises(ValueError, match="Image ID mismatch against export manifest"):
        import_image(img_file)


def test_cli_verify_subcommand(tmp_path: Path):
    img_file = tmp_path / "test.tar"
    img_file.write_bytes(b"data")
    digest = hashlib.sha256(b"data").hexdigest()
    write_sha256_file(tmp_path / "test.tar.sha256", digest, img_file.name)

    exit_code = main(["verify", "--archive", str(img_file)])
    assert exit_code == 0
