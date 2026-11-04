"""KB snapshot: move the knowledge base between machines as plain files, without the vectors.

The live KB is Postgres + pgvector. Its chunk text is ~19 MB; its vectors are ~90 MB and only valid
for the embedding model that made them. So a snapshot holds documents and their exact chunks, and
`import_snapshot` re-embeds the chunks with whatever model the importing team configured.

    data/kb/kb-snapshot.jsonl.gz   one JSON line per document: source, external_id, title, url,
                                   metadata, content_sha256, chunks (in order)
    data/kb/manifest.json          counts per source, file sha256, when and from what it was made

The leak guard still applies on import: a document or chunk the current case files withhold is skipped.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select, text

from .chunking import content_hash
from .models import kb_chunks, kb_documents
from .store import KBStore, _created_at_from_metadata, _format_vector

log = logging.getLogger(__name__)

SNAPSHOT_FILE = "kb-snapshot.jsonl.gz"
MANIFEST_FILE = "manifest.json"


def export_snapshot(store: KBStore, out_dir: Path) -> dict:
    """Write every document with its chunks (no vectors) to out_dir. Returns the manifest."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / SNAPSHOT_FILE
    counts: dict[str, int] = {}
    chunk_total = 0
    with store.engine.connect() as conn, gzip.open(path, "wt", encoding="utf-8", newline="\n", compresslevel=9) as fh:
        docs = conn.execute(
            select(kb_documents).order_by(kb_documents.c.source, kb_documents.c.external_id)
        ).mappings().all()
        for doc in docs:
            chunks = conn.execute(
                select(kb_chunks.c.content)
                .where(kb_chunks.c.document_id == doc["id"])
                .order_by(kb_chunks.c.chunk_index)
            ).scalars().all()
            fh.write(json.dumps({
                "source": doc["source"],
                "external_id": doc["external_id"],
                "title": doc["title"],
                "url": doc["url"],
                "content_sha256": doc["content_sha256"],
                "metadata": doc["doc_metadata"],
                "chunks": list(chunks),
            }, ensure_ascii=False, sort_keys=True) + "\n")
            counts[doc["source"]] = counts.get(doc["source"], 0) + 1
            chunk_total += len(chunks)
    manifest = {
        "file": SNAPSHOT_FILE,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "documents": sum(counts.values()),
        "chunks": chunk_total,
        "documents_by_source": dict(sorted(counts.items())),
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "embedding": "not included: `python -m anvil.kb import-snapshot` re-embeds with the configured model",
    }
    (out_dir / MANIFEST_FILE).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    return manifest


def read_snapshot(in_dir: Path) -> Iterator[dict]:
    with gzip.open(in_dir / SNAPSHOT_FILE, "rt", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


@dataclass
class ImportReport:
    documents_added: int = 0
    documents_skipped: int = 0  # already present with the same content
    documents_excluded: int = 0  # withheld by the current case files
    chunks_written: int = 0


def import_snapshot(store: KBStore, in_dir: Path, *, batch_chunks: int = 256) -> ImportReport:
    """Load a snapshot into the KB, embedding chunks in batches across documents."""
    manifest = json.loads((in_dir / MANIFEST_FILE).read_text(encoding="utf-8"))
    actual = hashlib.sha256((in_dir / SNAPSHOT_FILE).read_bytes()).hexdigest()
    if actual != manifest["sha256"]:
        raise ValueError(f"{SNAPSHOT_FILE} does not match its manifest (sha256 {actual[:12]} != "
                         f"{manifest['sha256'][:12]}): re-export or re-copy the snapshot")
    store.ensure_schema()
    report = ImportReport()
    pending: list[tuple[dict, list[str]]] = []
    pending_chunks = 0
    for doc in read_snapshot(in_dir):
        if not store.guard.allows(doc["source"], doc["external_id"]):
            report.documents_excluded += 1
            continue
        if _exists(store, doc):
            report.documents_skipped += 1
            continue
        chunks = [c for c in doc["chunks"] if store.guard.allows_content(c)]
        pending.append((doc, chunks))
        pending_chunks += len(chunks)
        if pending_chunks >= batch_chunks:
            _flush(store, pending, report)
            pending, pending_chunks = [], 0
    if pending:
        _flush(store, pending, report)
    log.info("import-snapshot: %s", report)
    return report


def _exists(store: KBStore, doc: dict) -> bool:
    with store.engine.connect() as conn:
        row = conn.execute(
            select(kb_documents.c.content_sha256).where(
                kb_documents.c.source == doc["source"], kb_documents.c.external_id == doc["external_id"]
            )
        ).first()
    if row and row.content_sha256 != doc["content_sha256"]:
        raise ValueError(f"{doc['source']} {doc['external_id']} already exists with different content; "
                         "import into an empty KB (or delete that document first)")
    return row is not None


def _flush(store: KBStore, pending: list[tuple[dict, list[str]]], report: ImportReport) -> None:
    texts = [c for _, chunks in pending for c in chunks]
    vectors = store.embedder.embed(texts) if texts else []
    if len(vectors) != len(texts):
        raise RuntimeError(f"embedder returned {len(vectors)} vectors for {len(texts)} chunks")
    it = iter(vectors)
    with store.engine.begin() as conn:
        for doc, chunks in pending:
            document_id = conn.execute(
                kb_documents.insert().values(
                    source=doc["source"], external_id=doc["external_id"], title=doc["title"], url=doc["url"],
                    content_sha256=doc["content_sha256"], doc_metadata=doc["metadata"] or {},
                    created_at=_created_at_from_metadata(doc["metadata"]),
                ).returning(kb_documents.c.id)
            ).scalar_one()
            rows = [{
                "document_id": document_id, "chunk_index": i, "content": chunk,
                "content_sha256": content_hash(chunk), "token_count": max(1, len(chunk) // 4),
                "embedding": _format_vector(next(it)),
            } for i, chunk in enumerate(chunks)]
            if rows:
                conn.execute(text(
                    "INSERT INTO kb_chunks (document_id, chunk_index, content, content_sha256, token_count, embedding) "
                    "VALUES (:document_id, :chunk_index, :content, :content_sha256, :token_count, "
                    "CAST(:embedding AS vector))"), rows)
            report.documents_added += 1
            report.chunks_written += len(rows)
