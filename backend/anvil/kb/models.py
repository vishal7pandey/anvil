"""SQLAlchemy Core tables for the knowledge base.

The KB owns its own ``kb_`` tables and does not import the API's ``Base``: the
control API (copilot) owns the Alembic history for its models, and merging two
declarative registries in one repo is how people lose an afternoon. The KB can
move to an Alembic revision once the API's migration base is merged.
"""

from __future__ import annotations

import logging

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)

EMBED_DIM = 1536

kb_metadata = MetaData()

kb_documents = Table(
    "kb_documents",
    kb_metadata,
    Column(
        "id",
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    ),
    Column("source", String(32), nullable=False),
    Column("external_id", String(255), nullable=False),
    Column("title", Text, nullable=False),
    Column("url", Text),
    Column("content_sha256", String(64), nullable=False),
    Column("doc_metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column(
        "fetched_at", DateTime(timezone=True), nullable=False, server_default=func.now()
    ),
    UniqueConstraint("source", "external_id", name="uq_kb_documents_source_external"),
)

kb_chunks = Table(
    "kb_chunks",
    kb_metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column(
        "document_id",
        UUID(as_uuid=True),
        ForeignKey("kb_documents.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("chunk_index", Integer, nullable=False),
    Column("content", Text, nullable=False),
    Column("content_sha256", String(64), nullable=False),
    Column("token_count", Integer, nullable=False),
    Column("embedding", Vector(EMBED_DIM), nullable=False),
    UniqueConstraint("document_id", "chunk_index", name="uq_kb_chunks_document_index"),
)

Index("ix_kb_chunks_document", kb_chunks.c.document_id)
Index("ix_kb_documents_source", kb_documents.c.source)
Index("ix_kb_documents_external", kb_documents.c.external_id)


def ensure_schema(engine: Engine) -> None:
    """Create the KB tables and indexes if they are missing.

    Idempotent; safe to call on every start and in the seed CLI.
    """
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    kb_metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_kb_chunks_embedding_hnsw "
                "ON kb_chunks USING hnsw (embedding vector_cosine_ops)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_kb_chunks_fts "
                "ON kb_chunks USING gin (to_tsvector('english', content))"
            )
        )
    log.debug("kb schema ensured")
