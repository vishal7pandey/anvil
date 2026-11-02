"""The agent-facing ``kb_search`` tool.

Kept as a plain function returning JSON-serialisable dicts so the runtime can
wrap it in whichever LangChain tool helper it prefers. Results carry the source
URL and upstream id, so evidence written to Jira/Confluence can cite them.
"""

from __future__ import annotations

from functools import lru_cache

from .config import KBSettings
from .store import KBStore


@lru_cache(maxsize=1)
def get_store() -> KBStore:
    return KBStore(KBSettings())


def kb_search(
    query: str, k: int = 8, sources: list[str] | None = None
) -> list[dict[str, object]]:
    """Search the RTEMS knowledge base.

    Args:
        query: natural-language question or keywords.
        k: maximum number of chunks to return.
        sources: optional filter, e.g. ``["rtems-docs"]`` or ``["gitlab-issue"]``.

    Returns:
        Chunks ranked by hybrid vector + full-text score, each with ``title``,
        ``content``, ``url``, ``source``, ``external_id`` and ``score``.
    """
    if not query.strip():
        return []
    hits = get_store().search(query, k=k, sources=sources)
    return [
        {
            "title": hit.title,
            "content": hit.content,
            "url": hit.url,
            "source": hit.source,
            "external_id": hit.external_id,
            "score": round(hit.score, 6),
        }
        for hit in hits
    ]
