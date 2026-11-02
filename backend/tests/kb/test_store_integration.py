"""Live pgvector tests.

Skipped unless ``ANVIL_TEST_DATABASE_URL`` is set, e.g.

    $env:ANVIL_TEST_DATABASE_URL="postgresql+psycopg://anvil:anvil@localhost:5433/anvil_kb_test"

Point it at a throwaway database: the fixture truncates the kb tables.
"""

import os

import pytest
from anvil.kb.chunking import content_hash
from anvil.kb.config import KBSettings
from anvil.kb.embeddings import DeterministicEmbedder
from anvil.kb.models import kb_documents
from anvil.kb.sources import SourceDocument
from anvil.kb.store import KBStore, _format_vector
from sqlalchemy import create_engine, insert, text

URL = os.environ.get("ANVIL_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(not URL, reason="ANVIL_TEST_DATABASE_URL not set")


@pytest.fixture(scope="module")
def store():
    engine = create_engine(URL, future=True)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - any failure means skip, not fail
        pytest.skip(f"test database unreachable: {exc}")
    settings = KBSettings(database_url=URL)
    instance = KBStore(settings, engine=engine, embedder=DeterministicEmbedder())
    instance.ensure_schema()
    yield instance
    instance.close()


@pytest.fixture(autouse=True)
def clean_tables(store):
    with store.engine.begin() as conn:
        conn.execute(text("TRUNCATE kb_chunks, kb_documents RESTART IDENTITY CASCADE"))


def _doc(source: str, external_id: str, title: str, body: str) -> SourceDocument:
    return SourceDocument(
        source=source,
        external_id=external_id,
        title=title,
        url=f"https://example.test/{external_id}",
        text=body,
        metadata={},
    )


def test_ingest_is_idempotent(store):
    document = _doc(
        "rtems-docs",
        "user/intro.rst",
        "Intro",
        "RTEMS scheduler overview.\n\nMore text.",
    )
    first = store.ingest([document])
    assert first.documents_added == 1
    assert first.chunks_written >= 1

    second = store.ingest([document])
    assert second.documents_skipped == 1
    assert second.chunks_written == 0


def test_changed_document_replaces_chunks(store):
    document = _doc("rtems-docs", "a.rst", "A", "original text about interrupts")
    store.ingest([document])
    updated = _doc("rtems-docs", "a.rst", "A", "rewritten text about interrupt latency")
    report = store.ingest([updated])
    assert report.documents_updated == 1
    with store.engine.connect() as conn:
        contents = conn.execute(text("SELECT content FROM kb_chunks")).scalars().all()
    assert contents == ["rewritten text about interrupt latency"]


def test_tc1_issue_is_never_ingested(store):
    report = store.ingest(
        [
            _doc("gitlab-issue", "5776", "pthread_mutex_init", "the withheld defect"),
            _doc("gitlab-issue", "6000", "safe issue", "an unrelated closed issue"),
        ]
    )
    assert report.documents_excluded == 1
    assert report.documents_added == 1
    assert store.stats()["documents"] == 1


def test_search_hides_tc1_even_if_inserted_directly(store):
    vector = _format_vector(
        DeterministicEmbedder().embed(["pthread mutex priority ceiling"])[0]
    )
    with store.engine.begin() as conn:
        document_id = conn.execute(
            insert(kb_documents)
            .values(
                source="gitlab-issue",
                external_id="5776",
                title="pthread_mutex_init writes flags before validating the priority ceiling",
                url="https://gitlab.example/issues/5776",
                content_sha256=content_hash("leak"),
                doc_metadata={},
            )
            .returning(kb_documents.c.id)
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO kb_chunks "
                "(document_id, chunk_index, content, content_sha256, token_count, embedding) "
                "VALUES (:document_id, 0, :content, :hash, 5, CAST(:embedding AS vector))"
            ),
            {
                "document_id": document_id,
                "content": "pthread mutex priority ceiling flags bug",
                "hash": content_hash("leak"),
                "embedding": vector,
            },
        )
    hits = store.search("pthread mutex priority ceiling", k=10)
    assert hits == []


def test_search_returns_ranked_citation_for_safe_document(store):
    store.ingest(
        [
            _doc(
                "rtems-docs",
                "sched.rst",
                "Scheduler",
                "Rate monotonic scheduling and priority ceilings.",
            ),
            _doc(
                "rtems-docs",
                "net.rst",
                "Networking",
                "lwIP stack and TCP throughput tuning.",
            ),
        ]
    )
    hits = store.search("priority ceiling scheduling", k=5)
    assert hits
    assert hits[0].external_id == "sched.rst"
    assert hits[0].url and hits[0].title == "Scheduler"


def test_source_filter(store):
    store.ingest(
        [
            _doc("rtems-docs", "sched.rst", "Scheduler", "priority ceiling scheduling"),
            _doc(
                "gitlab-issue",
                "6000",
                "Scheduler bug",
                "priority ceiling scheduling defect",
            ),
        ]
    )
    hits = store.search("priority ceiling", k=5, sources=["rtems-docs"])
    assert hits
    assert {hit.source for hit in hits} == {"rtems-docs"}
