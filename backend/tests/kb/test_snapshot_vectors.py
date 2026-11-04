"""ANVIL-58: snapshot vectors load only when the model matches; missing/mismatched fails loud."""

import gzip
import hashlib
import json
from types import SimpleNamespace

import pytest

from anvil.kb.snapshot import VECTORS_FILE, _load_vectors


def store(model="anvil-embed"):
    return SimpleNamespace(settings=SimpleNamespace(embed_model=model))


def write_snapshot(tmp_path, vectors=True, model="anvil-embed"):
    vec_path = tmp_path / VECTORS_FILE
    if vectors:
        with gzip.open(vec_path, "wt") as fh:
            fh.write(json.dumps({"source": "s", "external_id": "e",
                                 "vectors": [{"sha": "abc", "v": "[0.1,0.2]"}]}) + "\n")
    info = ({"model": model, "dim": 1536, "file": VECTORS_FILE,
             "sha256": hashlib.sha256(vec_path.read_bytes()).hexdigest()}
            if vectors else "not included")
    (tmp_path / "manifest.json").write_text(json.dumps({"embedding": info}))


def test_no_vectors_file_returns_none(tmp_path):
    write_snapshot(tmp_path, vectors=False)
    assert _load_vectors(tmp_path, store()) is None


def test_matching_model_loads_the_map(tmp_path):
    write_snapshot(tmp_path)
    assert _load_vectors(tmp_path, store()) == {"abc": "[0.1,0.2]"}


def test_mismatched_model_is_refused(tmp_path):
    write_snapshot(tmp_path, model="other-model")
    with pytest.raises(ValueError, match="other-model"):
        _load_vectors(tmp_path, store())


def test_declared_but_missing_file_fails(tmp_path):
    write_snapshot(tmp_path)
    (tmp_path / VECTORS_FILE).unlink()
    with pytest.raises(ValueError, match="missing"):
        _load_vectors(tmp_path, store())


def test_corrupt_vectors_file_fails(tmp_path):
    write_snapshot(tmp_path)
    with gzip.open(tmp_path / VECTORS_FILE, "wt") as fh:
        fh.write("{}\n")
    with pytest.raises(ValueError, match="sha256"):
        _load_vectors(tmp_path, store())
