"""ANVIL-60/59: local knowledge folder as a KB source and write-back target."""

import os

import pytest

from anvil.kb.local import LocalKnowledge, slugify
from anvil.kb.sources import SourceDocument


def test_slugify_makes_safe_names():
    assert slugify("RTEMS-1 evidence: root cause found") == "RTEMS-1-evidence-root-cause-found"
    assert slugify("###") == "page"


def test_write_page_lands_as_markdown_with_front_matter_and_versions(tmp_path):
    k = LocalKnowledge(tmp_path, store=object())  # object() has no .ingest -> kb_indexed False
    p1 = k.write_page("RTKB", "Post-mortem: first run", "# body\n")
    assert p1["version"] == 1 and p1["id"] == "RTKB/Post-mortem-first-run.md"
    text = (tmp_path / p1["id"]).read_text()
    assert "title: Post-mortem: first run" in text and "space: RTKB" in text and "# body" in text
    p2 = k.write_page("RTKB", "Post-mortem: first run", "# updated\n")
    assert p2["version"] == 2 and "created:" in (tmp_path / p1["id"]).read_text()


def test_search_and_get_roundtrip(tmp_path):
    k = LocalKnowledge(tmp_path, store=object())
    k.write_page("RTKB", "TC4 findings", "interrupt storm root cause")
    hits = k.search("interrupt storm")
    assert hits and hits[0]["title"] == "TC4 findings"
    page = k.get(hits[0]["id"])
    assert "interrupt storm" in page["body"] and page["version"] == 1
    with pytest.raises(Exception):
        k.get("../outside.md")


def test_documents_feed_ingest_local(tmp_path):
    k = LocalKnowledge(tmp_path, store=object())
    k.write_page("RTKB", "Page A", "content a")
    docs = list(k.documents())
    assert len(docs) == 1
    d = docs[0]
    assert d.source == "local" and d.external_id == "RTKB/Page-A.md"
    assert d.title == "Page A" and "content a" in d.text and "front" not in d.metadata["path"]


class _RecStore:
    """Captures ingested documents like KBStore.ingest does (leak guard lives in the real one)."""

    def __init__(self):
        from anvil.kb.store import IngestReport

        self.docs: list[SourceDocument] = []

    def ingest(self, docs):
        from anvil.kb.store import IngestReport

        got = list(docs)
        self.docs.extend(got)
        return IngestReport(len(got), 0, 0, 0, 0)


def test_write_page_indexes_into_the_kb_at_once(tmp_path):
    rec = _RecStore()
    k = LocalKnowledge(tmp_path, store=rec)
    page = k.write_page("RTKB", "run 1 post-mortem", "the fix was a watchdog reset")
    assert page["kb_indexed"] is True
    (doc,) = rec.docs
    assert doc.source == "local" and doc.external_id.endswith("run-1-post-mortem.md")
    assert "watchdog reset" in doc.text


# -- tool routing (run_tools) ---------------------------------------------------------------------


def _tool_names(tools):
    return {getattr(t, "__name__", "") for t in tools}


def _run(local=True):
    return {"run_id": "r1", "repo": r"C:\work\app" if local else "owner/repo",
            "jira_key": "K-1", "trigger": "local:K-1" if local else "jira",
            "base_ref": "main"}


def test_local_run_gets_knowledge_folder_tools(monkeypatch, tmp_path):
    from anvil.tools.run_scope import run_tools

    monkeypatch.setenv("ANVIL_KNOWLEDGE_DIR", str(tmp_path))
    monkeypatch.setenv("ANVIL_TICKETS_DIR", str(tmp_path / "tickets"))
    (tmp_path / "tickets" / "todo").mkdir(parents=True)
    tools = run_tools(_run(), sandbox=object())
    names = _tool_names(tools)
    for wanted in ("confluence_write", "confluence_search", "confluence_get", "publish_evidence"):
        assert wanted in names
    page = next(t for t in tools if getattr(t, "__name__", "") == "confluence_write")(
        "RTKB", "evidence page", "the fix")
    assert page["id"].startswith("RTKB/") and (tmp_path / page["id"]).exists()


def test_run_without_atlassian_falls_back_to_the_folder(monkeypatch, tmp_path):
    from anvil.tools._atlassian import AtlassianSettings
    from anvil.tools.run_scope import run_tools

    monkeypatch.setenv("ANVIL_KNOWLEDGE_DIR", str(tmp_path))
    monkeypatch.setattr(AtlassianSettings, "model_config",
                        AtlassianSettings.model_config)
    monkeypatch.setattr(AtlassianSettings, "atlassian_api_token", "", raising=False)
    monkeypatch.setattr(AtlassianSettings, "atlassian_email", "", raising=False)
    run = _run(local=False)
    tools = run_tools(run, sandbox=object())
    write = next(t for t in tools if getattr(t, "__name__", "") == "confluence_write")
    page = write("RTKB", "offline evidence", "no Atlassian")
    assert (tmp_path / page["id"]).exists()


def test_publish_evidence_writes_page_and_ticket_comment(monkeypatch, tmp_path):
    from anvil.tickets import LocalTracker
    from anvil.tools.run_scope import run_tools

    tickets = tmp_path / "tickets"
    (tickets / "todo").mkdir(parents=True)
    tracker = LocalTracker(tickets)
    tracker.create("RTEMS", "bug", "psx05 fails", "desc", ["anvil"])
    tracker.transition("RTEMS-1", "in-progress")
    monkeypatch.setenv("ANVIL_KNOWLEDGE_DIR", str(tmp_path / "kb"))
    monkeypatch.setenv("ANVIL_TICKETS_DIR", str(tickets))
    run = _run()
    tools = run_tools(run, sandbox=object())
    publish = next(t for t in tools if getattr(t, "__name__", "") == "publish_evidence")
    out = publish({"key": "RTEMS-1", "summary": "psx05 fails", "repo": "x/y",
                   "root_cause": "watchdog", "fix": "guard it", "run_id": "r1"})
    assert out["confluence_page"]["id"].startswith("RTKB/")
    assert (tmp_path / "kb" / out["confluence_page"]["id"]).exists()
    ticket = tracker.get("RTEMS-1")
    assert "watchdog" in str(ticket["comments"]) and "anvil-in-review" in ticket["labels"]


# -- live KB proof (needs Postgres; skip locally, required in CI) ----------------------------------

URL = os.environ.get("ANVIL_TEST_DATABASE_URL")


@pytest.mark.skipif(not URL, reason="ANVIL_TEST_DATABASE_URL not set")
def test_a_written_page_is_found_by_kb_search_straight_after(tmp_path):
    from sqlalchemy import create_engine, text

    from anvil.kb.config import KBSettings
    from anvil.kb.embeddings import DeterministicEmbedder
    from anvil.kb.store import KBStore

    engine = create_engine(URL, future=True)
    store = KBStore(KBSettings(database_url=URL), engine=engine, embedder=DeterministicEmbedder())
    store.ensure_schema()
    with store.engine.begin() as conn:
        conn.execute(text("TRUNCATE kb_chunks, kb_documents RESTART IDENTITY CASCADE"))
    try:
        k = LocalKnowledge(tmp_path, store=store)
        k.write_page("RTKB", "run 1 post-mortem",
                     "the psx05 failure was a spurious watchdog reset")
        hits = store.search("spurious watchdog reset", k=5, sources=["local"])
        assert hits and "watchdog" in hits[0].content
    finally:
        store.close()
        engine.dispose()
