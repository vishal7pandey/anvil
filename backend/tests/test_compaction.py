"""ANVIL-93: compaction is reported, and tunable through ANVIL_CONTEXT_WINDOW."""
import asyncio
from types import SimpleNamespace

from langchain_core.messages import HumanMessage

from anvil.analyze import analyze, to_markdown
from anvil.runtime import compaction
from anvil.runtime.compaction import compaction_info, model_profile, observed_summarization


def result(event):
    return SimpleNamespace(command=SimpleNamespace(update={"_summarization_event": event}))


EVENT = {"cutoff_index": 42, "summary_message": HumanMessage(content="x" * 300), "file_path": "/workspace/.anvil/h.md"}


def test_compaction_info_reads_the_summarization_event():
    assert compaction_info(result(EVENT)) == {"cutoff_index": 42, "summary_chars": 300,
                                              "history_file": "/workspace/.anvil/h.md"}


def test_no_info_when_nothing_was_compacted():
    assert compaction_info(SimpleNamespace(command=None)) is None          # a plain model response
    assert compaction_info(result(None)) is None
    assert compaction_info(SimpleNamespace(command=SimpleNamespace(update={"messages": []}))) is None


def test_observed_middleware_reports_sync_and_async_calls_and_passes_results_through(monkeypatch):
    class Fake:
        name = "SummarizationMiddleware"

        def __init__(self):
            self.answers = [result(EVENT), "plain"]

        def wrap_model_call(self, request, handler):
            return self.answers[0]

        async def awrap_model_call(self, request, handler):
            return self.answers[1]

    monkeypatch.setattr(compaction, "create_summarization_middleware", lambda model, backend: Fake())
    seen = []
    mw = observed_summarization(object(), object(), seen.append)
    assert mw.name == "SummarizationMiddleware"                # keeps the name: replaces deepagents' default
    first = mw.wrap_model_call("req", None)
    assert first.command.update["_summarization_event"] is EVENT and len(seen) == 1
    assert asyncio.run(mw.awrap_model_call("req", None)) == "plain" and len(seen) == 1


def test_context_window_env_gives_the_model_a_profile(monkeypatch):
    monkeypatch.delenv("ANVIL_CONTEXT_WINDOW", raising=False)
    assert model_profile() is None                              # unset = deepagents' old fallback
    monkeypatch.setenv("ANVIL_CONTEXT_WINDOW", "400000")
    assert model_profile() == {"max_input_tokens": 400000}
    for bad in ("", "abc", "0", "-5"):
        monkeypatch.setenv("ANVIL_CONTEXT_WINDOW", bad)
        assert model_profile() is None


def test_analyze_counts_compactions():
    def ev(seq, type_, data=None):
        return {"seq": seq, "ts": f"2026-11-04T12:00:{seq:02d}Z", "type": type_, "agent": "main", "data": data or {}}
    events = [ev(1, "run.status", {"status": "running"}), ev(2, "context.compacted", {"cutoff_index": 9}),
              ev(3, "context.compacted", {"cutoff_index": 20}), ev(4, "run.status", {"status": "succeeded"})]
    r = analyze(events)
    assert r["compactions"] == 2
    assert "Context compactions: 2" in to_markdown("r1", r)


def test_real_agent_builds_with_the_observed_middleware_replacing_the_default(monkeypatch):
    """create_agent refuses two middleware with one name, so a clean build means ours replaced deepagents' own."""
    from unittest.mock import MagicMock

    from anvil.runtime.agent import RunConfig, build_agent
    from anvil.runtime.sandbox import DockerSandbox

    monkeypatch.setenv("ANVIL_CONTEXT_WINDOW", "400000")
    seen = []
    graph = build_agent(RunConfig(run_id="x", repo="r", mode="interactive"), DockerSandbox(MagicMock()),
                        on_compact=seen.append)
    assert graph is not None and seen == []
