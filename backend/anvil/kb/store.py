"""Ingestion and retrieval for the knowledge base."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Self

from sqlalchemy import (
    String,
    bindparam,
    create_engine,
    delete,
    func,
    insert,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.engine import Engine

from .chunking import chunk_text, content_hash
from .config import KBSettings
from .embeddings import Embedder, GatewayEmbedder
from .exclusions import ExclusionGuard
from .models import EMBED_DIM, ensure_schema, kb_chunks, kb_documents
from .sources import SourceDocument

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class IngestReport:
    documents_added: int = 0
    documents_updated: int = 0
    documents_skipped: int = 0
    documents_excluded: int = 0
    chunks_written: int = 0

    @property
    def documents_seen(self) -> int:
        return (
            self.documents_added
            + self.documents_updated
            + self.documents_skipped
            + self.documents_excluded
        )


@dataclass(frozen=True)
class SearchHit:
    chunk_id: int
    document_id: str
    source: str
    external_id: str
    title: str
    url: str | None
    content: str
    score: float


def _created_at_from_metadata(metadata: dict | None) -> datetime | None:
    """The upstream creation time recorded by the crawler (ANVIL-43, ANVIL-68), or None."""
    meta = metadata or {}
    value = meta.get("created_at") or meta.get("createdAt")
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _doc_created_at(document: SourceDocument) -> datetime | None:
    """The upstream creation time for an ingested document (ANVIL-43)."""
    return _created_at_from_metadata(document.metadata)


class KBStore:
    def __init__(
        self,
        settings: KBSettings | None = None,
        *,
        engine: Engine | None = None,
        embedder: Embedder | None = None,
        guard: ExclusionGuard | None = None,
    ) -> None:
        self.settings = settings or KBSettings()
        if self.settings.embed_dim != EMBED_DIM:
            raise ValueError(
                f"ANVIL_EMBED_DIM={self.settings.embed_dim} does not match "
                f"the kb_chunks vector({EMBED_DIM}) schema"
            )
        self.engine = engine or create_engine(self.settings.database_url, future=True)
        self._owns_engine = engine is None
        self.embedder = embedder or GatewayEmbedder(self.settings)
        self.guard = guard or ExclusionGuard(
            self.settings.excluded_issue_ids,
            self.settings.excluded_mr_ids,
            self.settings.excluded_commit_prefixes,
        )

    def close(self) -> None:
        if self._owns_engine:
            self.engine.dispose()
        close = getattr(self.embedder, "close", None)
        if callable(close):
            close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def ensure_schema(self) -> None:
        ensure_schema(self.engine)

    def ingest(self, documents: Iterable[SourceDocument]) -> IngestReport:
        added = updated = skipped = excluded = chunks_written = 0
        for document in documents:
            if not self.guard.allows(document.source, document.external_id):
                excluded += 1
                log.info(
                    "excluded %s %s from ingestion",
                    document.source,
                    document.external_id,
                )
                continue
            outcome, chunks = self._ingest_one(document)
            if outcome == "added":
                added += 1
                chunks_written += chunks
            elif outcome == "updated":
                updated += 1
                chunks_written += chunks
            else:
                skipped += 1
        report = IngestReport(added, updated, skipped, excluded, chunks_written)
        log.info("ingest: %s", report)
        return report

    def _ingest_one(self, document: SourceDocument) -> tuple[str, int]:
        digest = content_hash(document.text)
        with self.engine.begin() as conn:
            existing = conn.execute(
                select(kb_documents.c.id, kb_documents.c.content_sha256).where(
                    kb_documents.c.source == document.source,
                    kb_documents.c.external_id == document.external_id,
                )
            ).first()
            if existing and existing.content_sha256 == digest:
                return "skipped", 0

            if existing:
                document_id = existing.id
                conn.execute(
                    update(kb_documents)
                    .where(kb_documents.c.id == document_id)
                    .values(
                        title=document.title,
                        url=document.url,
                        content_sha256=digest,
                        doc_metadata=document.metadata,
                        created_at=_doc_created_at(document),
                        fetched_at=func.now(),
                    )
                )
                conn.execute(
                    delete(kb_chunks).where(kb_chunks.c.document_id == document_id)
                )
                outcome = "updated"
            else:
                document_id = conn.execute(
                    insert(kb_documents)
                    .values(
                        source=document.source,
                        external_id=document.external_id,
                        title=document.title,
                        url=document.url,
                        content_sha256=digest,
                        doc_metadata=document.metadata,
                        created_at=_doc_created_at(document),
                    )
                    .returning(kb_documents.c.id)
                ).scalar_one()
                outcome = "added"

            chunks = chunk_text(
                document.text,
                max_chars=self.settings.chunk_chars,
                overlap_chars=self.settings.chunk_overlap,
            )
            chunks = [c for c in chunks if self.guard.allows_content(c)]
            if not chunks:
                return outcome, 0
            vectors = self.embedder.embed(chunks)
            if len(vectors) != len(chunks):
                raise RuntimeError(
                    f"embedder returned {len(vectors)} vectors for {len(chunks)} chunks"
                )
            conn.execute(
                text(
                    "INSERT INTO kb_chunks "
                    "(document_id, chunk_index, content, content_sha256, token_count, embedding) "
                    "VALUES (:document_id, :chunk_index, :content, :content_sha256, :token_count, "
                    "CAST(:embedding AS vector))"
                ),
                [
                    {
                        "document_id": document_id,
                        "chunk_index": index,
                        "content": chunk,
                        "content_sha256": content_hash(chunk),
                        "token_count": max(1, len(chunk) // 4),
                        "embedding": _format_vector(vector),
                    }
                    for index, (chunk, vector) in enumerate(
                        zip(chunks, vectors, strict=True)
                    )
                ],
            )
            return outcome, len(chunks)

    def search(
        self,
        query: str,
        *,
        k: int = 8,
        candidate_limit: int = 50,
        sources: Sequence[str] | None = None,
        as_of: str | None = None,
    ) -> list[SearchHit]:
        """Hybrid vector + full-text search.

        ``as_of`` (ISO date/datetime) is the case's leak boundary: documents created after it are
        "from the future" and hidden (ANVIL-43). When ``as_of`` is set, undated documents (NULL
        ``created_at``) are excluded by default to ensure answer leaks cannot occur through undated
        pages (ANVIL-68). When ``as_of`` is None the filter is inert and all documents are visible
        (normal TC4 path).
        """
        query = query.strip()
        if not query:
            return []
        if k <= 0:
            raise ValueError("k must be positive")
        vector = self.embedder.embed([query])[0]
        if len(vector) != EMBED_DIM:
            raise ValueError(
                f"query embedding has dimension {len(vector)}, expected {EMBED_DIM}"
            )
        params = {
            "qvec": _format_vector(vector),
            "q": query,
            "deny": list(self.settings.excluded_issue_ids),
            "cand": max(candidate_limit, k),
            "k": k,
            "as_of": as_of,
        }
        statement = text(_SEARCH_SQL).bindparams(
            bindparam("deny", type_=ARRAY(String)),
            bindparam("sources", type_=ARRAY(String)),
        )
        with self.engine.connect() as conn:
            rows = conn.execute(
                statement, {**params, "sources": list(sources) if sources else None}
            )
            hits = [
                SearchHit(
                    chunk_id=row.id,
                    document_id=str(row.document_id),
                    source=row.source,
                    external_id=row.external_id,
                    title=row.title,
                    url=row.url,
                    content=row.content,
                    score=float(row.score),
                )
                for row in rows
            ]
        return [
            hit
            for hit in hits
            if self.guard.allows(hit.source, hit.external_id)
            and self.guard.allows_content(hit.content)
        ]

    def stats(self) -> dict[str, int]:
        with self.engine.connect() as conn:
            documents = conn.execute(
                select(func.count()).select_from(kb_documents)
            ).scalar_one()
            chunks = conn.execute(
                select(func.count()).select_from(kb_chunks)
            ).scalar_one()
        return {"documents": int(documents), "chunks": int(chunks)}


def _format_vector(vector: Sequence[float]) -> str:
    return "[" + ",".join(f"{float(value):.8g}" for value in vector) + "]"


_SEARCH_SQL = """
WITH excluded AS (
    SELECT id FROM kb_documents
    WHERE source = 'gitlab-issue' AND external_id = ANY(:deny)
),
visible_docs AS (
    SELECT id FROM kb_documents
    WHERE CAST(:as_of AS timestamptz) IS NULL
       OR (created_at IS NOT NULL AND created_at <= CAST(:as_of AS timestamptz))
),
vec AS (
    SELECT c.id, row_number() OVER (ORDER BY c.embedding <=> CAST(:qvec AS vector)) AS rn
    FROM kb_chunks c
    WHERE c.document_id NOT IN (SELECT id FROM excluded)
      AND c.document_id IN (SELECT id FROM visible_docs)
    ORDER BY c.embedding <=> CAST(:qvec AS vector)
    LIMIT :cand
),
fts AS (
    SELECT c.id, row_number() OVER (
        ORDER BY ts_rank_cd(to_tsvector('english', c.content), websearch_to_tsquery('english', :q)) DESC
    ) AS rn
    FROM kb_chunks c
    WHERE c.document_id NOT IN (SELECT id FROM excluded)
      AND c.document_id IN (SELECT id FROM visible_docs)
      AND to_tsvector('english', c.content) @@ websearch_to_tsquery('english', :q)
    ORDER BY ts_rank_cd(to_tsvector('english', c.content), websearch_to_tsquery('english', :q)) DESC
    LIMIT :cand
)
SELECT c.id, c.document_id, c.content, d.title, d.url, d.source, d.external_id,
       COALESCE(1.0 / (60 + vec.rn), 0) + COALESCE(1.0 / (60 + fts.rn), 0) AS score
FROM kb_chunks c
JOIN kb_documents d ON d.id = c.document_id
LEFT JOIN vec ON vec.id = c.id
LEFT JOIN fts ON fts.id = c.id
WHERE (vec.id IS NOT NULL OR fts.id IS NOT NULL)
  AND (:sources IS NULL OR d.source = ANY(:sources))
ORDER BY score DESC, c.id
LIMIT :k
"""
