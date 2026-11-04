"""The KB has its own database setting, and an unavailable KB is a tool error, not a dead run (ANVIL-51)."""

import pytest
from sqlalchemy.exc import ProgrammingError

from anvil.kb import tool
from anvil.kb.config import DEFAULT_DATABASE_URL, KBSettings, kb_database_url
from anvil.tools.errors import ToolError, returning_errors

RUN_DB = "postgresql+psycopg://anvil:anvil@localhost:5433/anvil_claude"
KB_DB = "postgresql+psycopg://anvil:anvil@localhost:5433/anvil"


def test_kb_database_url_prefers_kb_setting_then_run_database(monkeypatch):
    monkeypatch.setenv("ANVIL_DATABASE_URL", RUN_DB)
    monkeypatch.setenv("ANVIL_KB_DATABASE_URL", KB_DB)
    assert kb_database_url() == KB_DB and KBSettings().database_url == KB_DB
    monkeypatch.setenv("ANVIL_KB_DATABASE_URL", "")
    assert kb_database_url() == RUN_DB
    monkeypatch.delenv("ANVIL_KB_DATABASE_URL")
    monkeypatch.delenv("ANVIL_DATABASE_URL")
    assert kb_database_url() == DEFAULT_DATABASE_URL


class _MissingTableStore:
    def search(self, *a, **kw):
        raise ProgrammingError("SELECT ...", {}, Exception('relation "kb_documents" does not exist'))


def test_missing_kb_table_comes_back_to_the_agent(monkeypatch):
    # TC3 run f9181d40: the first kb_search on a database without the KB ended the whole run.
    monkeypatch.setattr(tool, "get_store", lambda: _MissingTableStore())
    with pytest.raises(ToolError, match='knowledge base unavailable .*kb_documents" does not exist'):
        tool.make_kb_search()("telnetd authentication")
    out = returning_errors(tool.make_kb_search())("telnetd authentication")
    assert out["ok"] is False and "continue without kb_search" in out["error"]
