"""web_search tool: OpenAI's built-in web search through the gateway, with a per-run domain blocklist.

The blocklist is an answer-leak control for evaluation runs: the upstream issue tracker, mailing
lists and our mirrors hold the withheld fixes. Every page the model *opened* or *cited* is checked,
and a result that touched a blocked domain is withheld as a whole: the summary text itself could
carry the answer, so dropping only the citation would not be enough.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from typing import Any
from urllib.parse import urlparse

import httpx

# Upstream RTEMS tracker/lists/source and our mirrors: where withheld fixes live.
TC_BLOCKLIST = (
    "gitlab.rtems.org",
    "git.rtems.org",
    "devel.rtems.org",
    "lists.rtems.org",
    "github.com/rtems",
    "github.com/vishal7pandey",
)
WITHHELD = ("Search results withheld: every answer drew on sources that are blocked for this run "
            "(they may contain the solution under evaluation). Work from the code and the ticket.")


def blocked(url: str, blocklist: Iterable[str]) -> bool:
    parsed = urlparse(url if "//" in url else f"//{url}")
    host = (parsed.hostname or "").lower().removeprefix("www.")
    path = parsed.path.lower()
    for entry in blocklist:
        domain, _, prefix = entry.lower().partition("/")
        if host == domain or host.endswith("." + domain):
            segments = [seg for seg in path.split("/") if seg]
            wanted = [seg for seg in prefix.split("/") if seg]
            if segments[: len(wanted)] == wanted:  # whole path segments, so rtems != rtemsfoo
                return True
    return False


def _parse(response: dict[str, Any]) -> tuple[str, list[str], list[str]]:
    """Return (answer text, cited urls, all touched urls)."""
    text_parts: list[str] = []
    cited: list[str] = []
    touched: list[str] = []
    for item in response.get("output", []):
        if item.get("type") == "web_search_call":
            action = item.get("action") or {}
            if action.get("url"):
                touched.append(action["url"])
            touched.extend(s.get("url", "") for s in action.get("sources") or [] if s.get("url"))
        elif item.get("type") == "message":
            for content in item.get("content", []):
                text_parts.append(content.get("text", ""))
                cited.extend(a.get("url", "") for a in content.get("annotations", []) if a.get("url"))
    return "\n".join(text_parts).strip(), cited, touched + cited


def make_web_search(blocklist: Iterable[str] = (), *, gateway_url: str | None = None, api_key: str | None = None,
                    model: str = "anvil-fast", run_id: str | None = None,
                    client: httpx.Client | None = None) -> Callable[[str], str]:
    blocklist = tuple(blocklist)
    base = (gateway_url or os.environ.get("ANVIL_GATEWAY_URL", "http://localhost:4000/v1")).rstrip("/")
    key = api_key or os.environ.get("LITELLM_MASTER_KEY", "sk-anvil-local")
    http = client or httpx.Client(timeout=180)

    def _ask(query: str) -> dict[str, Any]:
        r = http.post(f"{base}/responses", headers={"Authorization": f"Bearer {key}"},
                      json={"model": model, "input": query, "tools": [{"type": "web_search"}],
                            "metadata": {"run_id": run_id or "", "tool": "web_search"}})
        r.raise_for_status()
        return r.json()

    def web_search(query: str) -> str:
        """Search the web and get a short, cited answer.

        Use for current external facts: standards and regulations (e.g. the EU Cyber Resilience
        Act), upstream release notes, CVEs, tool documentation. Not for this repository's code:
        read the code instead.

        Args:
            query: A specific question, e.g. "lwIP 2.2.1 release notes ACD changes".
        """
        attempts = [query]
        if blocklist:
            attempts.append(f"{query}\n\nDo not open or cite these sources: {', '.join(blocklist)}.")
        for attempt in attempts:
            try:
                text, cited, touched = _parse(_ask(attempt))
            except httpx.HTTPError as exc:
                return f"web_search failed: {type(exc).__name__}"
            if not any(blocked(u, blocklist) for u in touched):
                sources = "\n".join(f"- {u}" for u in dict.fromkeys(cited)) or "- (no citations)"
                return f"{text}\n\nSources:\n{sources}"
        return WITHHELD

    return web_search
