"""The agent-facing ``kb_search`` tool.

Kept as a plain function returning JSON-serialisable dicts so the runtime can
wrap it in whichever LangChain tool helper it prefers. Results carry the source
URL and upstream id, so evidence written to Jira/Confluence can cite them.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Callable

from sqlalchemy.exc import SQLAlchemyError

from anvil.tools.errors import ToolError

from .config import KBSettings
from .store import KBStore


@lru_cache(maxsize=1)
def get_store() -> KBStore:
    return KBStore(KBSettings())


def _search(query: str, k: int, sources: list[str] | None, as_of: str | None) -> list[dict[str, object]]:
    if not query.strip():
        return []
    try:
        hits = get_store().search(query, k=k, sources=sources, as_of=as_of)
    except SQLAlchemyError as exc:
        # An unavailable KB is an outward failure, not a reason to end the run: the agent gets it
        # as the tool result and works from the checkout and the web (TC3 run f9181d40, ANVIL-51).
        cause = str(getattr(exc, "orig", None) or exc).splitlines()[0][:200]
        raise ToolError(
            f"knowledge base unavailable ({cause}); continue without kb_search and say in the "
            "evidence that the KB was not consulted"
        ) from exc
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


def make_kb_search(as_of: str | None = None) -> Callable[..., list[dict[str, Any]]]:
    """Bind ``kb_search`` to a case's leak boundary on the host side (ANVIL-43).

    ``as_of`` comes from the run's case (resolved by the API/CLI from ``run["case_id"]`` or the
    run's Jira key), never from the model: documents created after it are hidden from the search.
    ``None`` means no case is active and results are unchanged.
    """

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
        return _search(query, k, sources, as_of)

    return kb_search


kb_search = make_kb_search()
