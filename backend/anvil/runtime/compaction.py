"""Context compaction, made visible and tunable (ANVIL-93).

deepagents already compacts: its SummarizationMiddleware summarizes older messages when the context
grows past a threshold, keeps the recent ones verbatim, and appends what it evicted to
`<artifacts_root>/conversation_history/<session>.md` (here /workspace/.anvil/..., inside the sandbox)
so the agent can re-read it. Two things were missing:

- **Visibility.** Compaction happened silently, so a run that "forgot" a fact could not be explained.
  `observed_summarization` builds the same middleware and reports every compaction through
  `on_compact`; the executor turns that into a `context.compacted` run event (UI, logs, analyze).
- **Tuning.** Our gateway aliases carry no model profile, so deepagents falls back to "summarize at
  170k tokens, keep 6 messages" whatever the real model is. `ANVIL_CONTEXT_WINDOW` (tokens) gives the
  model a profile, which switches deepagents to its fraction defaults (summarize at 85% of the
  window, keep the last 10%). Unset = exactly the old behaviour.

The middleware keeps its name, "SummarizationMiddleware", so passing it to `create_deep_agent`
replaces the default in place instead of adding a second one.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from deepagents.middleware.summarization import SUMMARIZATION_EVENT_KEY, create_summarization_middleware


def context_window() -> int | None:
    """ANVIL_CONTEXT_WINDOW: the context window of the models behind the gateway aliases, in tokens."""
    raw = os.environ.get("ANVIL_CONTEXT_WINDOW", "").strip()
    return int(raw) if raw.isdigit() and int(raw) > 0 else None


def model_profile() -> dict[str, int] | None:
    window = context_window()
    return {"max_input_tokens": window} if window else None


def compaction_info(result: Any) -> dict[str, Any] | None:
    """What a SummarizationMiddleware result says about a compaction, or None if there was none."""
    update = getattr(getattr(result, "command", None), "update", None)
    event = update.get(SUMMARIZATION_EVENT_KEY) if isinstance(update, dict) else None
    if not event:
        return None
    content = getattr(event.get("summary_message"), "content", "")
    return {"cutoff_index": int(event.get("cutoff_index") or 0),
            "summary_chars": len(content if isinstance(content, str) else str(content)),
            "history_file": event.get("file_path")}


def observed_summarization(model: Any, backend: Any, on_compact: Callable[[dict[str, Any]], None]) -> Any:
    """deepagents' summarization middleware, reporting each compaction to `on_compact`."""
    middleware = create_summarization_middleware(model, backend)
    sync_call, async_call = middleware.wrap_model_call, middleware.awrap_model_call

    def note(result: Any) -> Any:
        info = compaction_info(result)
        if info:
            on_compact(info)
        return result

    def wrap_model_call(request: Any, handler: Any) -> Any:
        return note(sync_call(request, handler))

    async def awrap_model_call(request: Any, handler: Any) -> Any:
        return note(await async_call(request, handler))

    middleware.wrap_model_call = wrap_model_call
    middleware.awrap_model_call = awrap_model_call
    return middleware
