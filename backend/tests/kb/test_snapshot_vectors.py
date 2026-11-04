"""ANVIL-58: the vectors sidecar loads only when model, dim and snapshot pairing all match."""

import gzip
import hashlib
import json
from types import SimpleNamespace

import pytest

from anvil.kb.snapshot import MANIFEST_FILE, VECTORS_FILE, VECTORS_MANIFEST, _load_vectors

SNAP_SHA = "f" * 64


def store(model="anvil-embed", dim=1536):
    return SimpleNamespace(settings=SimpleNamespace(embed_model=model, embed_dim=dim))


def write_snapshot(tmp_path, vectors=True, model="anvil-embed", dim=1536, snap_sha=SNAP_SHA,
                   write_sidecar=True):
    # the committed manifest: always text-only
    (tmp_path / MANIFEST_FILE).write_text(json.dumps({"sha256": SNAP_SHA, "embedding": "not included"}))
    vec_path = tmp_path / VECTORS_FILE
    if vectors:
        with gzip.open(vec_path, "wt") as fh:
            fh.write(json.dumps({"source": "s", "external_id": "e",
                                 "vectors": [{"sha": "abc", "v": "[0.1,0.2]"}]}) + "\n")
    if write_sidecar:
        (tmp_path / VECTORS_MANIFEST).write_text(json.dumps({
            "model": model, "dim": dim, "file": VECTORS_FILE,
            "sha256": hashlib.sha256(vec_path.read_bytes()).hexdigest() if vectors else "0" * 64,
            "snapshot_sha256": snap_sha,
        }))


def test_no_sidecar_returns_none_and_reembeds(tmp_path):
    write_snapshot(tmp_path, write_sidecar=False)
    assert _load_vectors(tmp_path, store()) is None


def test_matching_sidecar_loads_the_map(tmp_path):
    write_snapshot(tmp_path)
    assert _load_vectors(tmp_path, store()) == {"abc": "[0.1,0.2]"}


@pytest.mark.parametrize("field,kw", [
    ("model", {"model": "other-model"}),
    ("dim", {"dim": 768}),
    ("snapshot", {"snap_sha": "0" * 64}),
])
def test_mismatch_is_refused(tmp_path, field, kw):
    write_snapshot(tmp_path, **kw)
    with pytest.raises(ValueError):
        _load_vectors(tmp_path, store())


def test_declared_but_missing_file_fails(tmp_path):
    write_snapshot(tmp_path, vectors=False)  # sidecar written, file missing
    with pytest.raises(ValueError, match="missing"):
        _load_vectors(tmp_path, store())


def test_corrupt_vectors_file_fails(tmp_path):
    write_snapshot(tmp_path)
    with gzip.open(tmp_path / VECTORS_FILE, "wt") as fh:
        fh.write("{}\n")
    with pytest.raises(ValueError, match="sha256"):
        _load_vectors(tmp_path, store())
