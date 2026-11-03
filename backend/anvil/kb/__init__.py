"""Anvil knowledge base: ingestion, pgvector storage and ``kb_search``.

Import-light on purpose: the API and runtime import this package without a
database connection or an embedding key. Open a :class:`KBStore` when you need
one.
"""

from .config import TC1_EXCLUDED_ISSUE_IDS, KBSettings
from .exclusions import ExclusionGuard
from .store import IngestReport, KBStore, SearchHit
from .tool import kb_search

__all__ = [
    "TC1_EXCLUDED_ISSUE_IDS",
    "ExclusionGuard",
    "IngestReport",
    "KBSettings",
    "KBStore",
    "SearchHit",
    "kb_search",
]
