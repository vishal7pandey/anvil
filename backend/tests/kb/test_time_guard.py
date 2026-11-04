"""ANVIL-43: the time-based KB leak guard.

Two layers, both tested:

* unit: ``created_at`` is parsed from crawler metadata, ``make_kb_search`` binds a fixed ``as_of``
  (never model-chosen), and the API resolves the case from the run, not the model;
* live (skipped without ``ANVIL_TEST_DATABASE_URL``): a document created after the case's
  ``as_of`` is hidden from search during that case, and visible without it.
"""

from datetime import datetime, timezone

import pytest
from anvil.kb.tool import make_kb_search
from anvil.kb.store import _doc_created_at
from anvil.kb.sources import SourceDocument


class _Doc:
    def __init__(self, metadata):
        self.metadata = metadata


def test_doc_created_at_from_metadata():
    doc = _Doc({"created_at": "2026-01-05T12:00:00Z"})
    assert _doc_created_at(doc) == datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)
    assert _doc_created_at(_Doc({})) is None
    assert _doc_created_at(_Doc({"created_at": "not-a-date"})) is None
    naive = _doc_created_at(_Doc({"created_at": datetime(2026, 1, 1)}))
    assert naive is not None and naive.tzinfo == timezone.utc


def test_make_kb_search_passes_as_of_and_never_takes_it_from_the_caller(monkeypatch):
    seen = {}

    class FakeStore:
        def search(self, query, *, k, sources, as_of):
            seen.update(query=query, k=k, sources=sources, as_of=as_of)
            return []

    monkeypatch.setattr("anvil.kb.tool.get_store", lambda: FakeStore())
    assert make_kb_search("2026-10-08")("counter overflow") == []
    assert seen["as_of"] == "2026-10-08"
    # The model-facing signature has no as_of parameter: it cannot widen its own window.
    import inspect

    assert "as_of" not in inspect.signature(make_kb_search("x")).parameters


def test_unbound_kb_search_has_no_as_of(monkeypatch):
    seen = {}

    class FakeStore:
        def search(self, query, *, k, sources, as_of):
            seen["as_of"] = as_of
            return []

    monkeypatch.setattr("anvil.kb.tool.get_store", lambda: FakeStore())
    assert make_kb_search()("anything") == []
    assert seen["as_of"] is None


def test_case_as_of_resolves_by_case_id_and_jira_key():
    from anvil.api.runtime_factory import create_executor_from_environment  # noqa

    # The resolver is a closure inside create_executor_from_environment; test the lookup logic
    # directly against load_cases to keep it honest without booting the factory.
    from anvil.cases import load_cases

    cases = {c.id: c for c in load_cases()}
    by_key = {c.ticket_key: c for c in cases.values() if c.ticket_key}
    assert by_key["RTEMS-1"].as_of == "2026-09-20"      # rtems-5776
    assert by_key["RTEMS-7"].as_of == "2026-10-08"      # rtems-5806
    assert cases["rtems-5776"].ticket_key == "RTEMS-1"


# --- live pgvector tests (skipped without ANVIL_TEST_DATABASE_URL) -----------------------------

import os
from anvil.kb.chunking import content_hash
from anvil.kb.config import KBSettings
from anvil.kb.embeddings import DeterministicEmbedder
from anvil.kb.models import kb_documents
from anvil.kb.store import KBStore, _format_vector
from sqlalchemy import create_engine, insert, text

URL = os.environ.get("ANVIL_TEST_DATABASE_URL")


@pytest.fixture(scope="module")
def store():
    if not URL:
        pytest.skip("ANVIL_TEST_DATABASE_URL not set")
    engine = create_engine(URL, future=True)
    settings = KBSettings(database_url=URL)
    instance = KBStore(settings, engine=engine, embedder=DeterministicEmbedder())
    instance.ensure_schema()
    yield instance
    instance.close()


@pytest.fixture
def clean_tables(store):
    with store.engine.begin() as conn:
        conn.execute(text("TRUNCATE kb_chunks, kb_documents RESTART IDENTITY CASCADE"))


def _insert_doc(store, external_id, created_at, text_body):
    vector = _format_vector(DeterministicEmbedder().embed([text_body])[0])
    with store.engine.begin() as conn:
        document_id = conn.execute(
            insert(kb_documents)
            .values(
                source="gitlab-issue",
                external_id=external_id,
                title=text_body,
                url="https://example.test/" + external_id,
                content_sha256=content_hash(text_body),
                doc_metadata={},
                created_at=created_at,
            )
            .returning(kb_documents.c.id)
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO kb_chunks (document_id, chunk_index, content, content_sha256, "
                "token_count, embedding) VALUES (:document_id, 0, :content, :hash, 5, "
                "CAST(:embedding AS vector))"
            ),
            {"document_id": document_id, "content": text_body,
             "hash": content_hash(text_body), "embedding": vector},
        )


def test_search_hides_documents_created_after_as_of(store, clean_tables):
    _insert_doc(store, "old-1", datetime(2026, 1, 1, tzinfo=timezone.utc), "mutex priority ceiling old")
    _insert_doc(store, "new-1", datetime(2026, 10, 20, tzinfo=timezone.utc), "mutex priority ceiling new")
    _insert_doc(store, "doc-1", None, "mutex priority ceiling undated")

    query = "mutex priority ceiling"
    all_hits = store.search(query, k=10)
    assert {h.external_id for h in all_hits} == {"old-1", "new-1", "doc-1"}

    bounded = store.search(query, k=10, as_of="2026-09-01")
    ids = {h.external_id for h in bounded}
    assert "new-1" not in ids          # after as_of: "from the future"
    assert {"old-1", "doc-1"} <= ids   # before as_of and undated: visible


def test_ingest_writes_created_at_from_metadata(store, clean_tables):
    doc = SourceDocument(
        source="gitlab-issue", external_id="6001", title="x",
        url=None, text="counter conversion overflow test",
        metadata={"created_at": "2026-10-08T22:23:52Z"},
    )
    store.ingest([doc])
    with store.engine.connect() as conn:
        created = conn.execute(
            text("SELECT created_at FROM kb_documents WHERE external_id='6001'")
        ).scalar_one()
    assert created == datetime(2026, 10, 8, 22, 23, 52, tzinfo=timezone.utc)


def test_snapshot_import_sets_created_at(store, clean_tables, tmp_path):
    # Review fix: a KB loaded from data/kb must keep the time guard working (ANVIL-43).
    import gzip
    import hashlib
    import json as js

    from anvil.kb.snapshot import MANIFEST_FILE, SNAPSHOT_FILE, import_snapshot

    lines = [
        {"source": "gitlab-issue", "external_id": "rtems/rtos/rtems#6002", "title": "x",
         "url": None, "content_sha256": content_hash("a"),
         "metadata": {"created_at": "2026-10-08T22:23:52Z"}, "chunks": ["overflow body a"]},
        {"source": "rtems-docs", "external_id": "d.rst", "title": "y", "url": None,
         "content_sha256": content_hash("b"), "metadata": {}, "chunks": ["overflow body b"]},
    ]
    with gzip.open(tmp_path / SNAPSHOT_FILE, "wt", encoding="utf-8") as fh:
        fh.writelines(js.dumps(line) + "\n" for line in lines)
    (tmp_path / MANIFEST_FILE).write_text(
        js.dumps({"sha256": hashlib.sha256((tmp_path / SNAPSHOT_FILE).read_bytes()).hexdigest()}))
    report = import_snapshot(store, tmp_path)
    assert report.documents_added == 2
    with store.engine.connect() as conn:
        rows = {r.external_id: r.created_at for r in conn.execute(text(
            "SELECT external_id, created_at FROM kb_documents"))}
    assert rows["rtems/rtos/rtems#6002"] == datetime(2026, 10, 8, 22, 23, 52, tzinfo=timezone.utc)
    assert rows["d.rst"] is None
