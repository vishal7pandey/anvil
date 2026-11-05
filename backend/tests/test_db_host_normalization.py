"""ANVIL-101: on Windows a `localhost` database host made every new connection wait about 30 s (IPv6 first).

The API's own engines were already normalised, but Alembic (migrations/env.py reads Settings.database_dsn) and the
KB store (kb_database_url) were not, so a `.env` that said `localhost` gave a 138 s migration and a 6-minute
snapshot import. The rewrite is Windows-only by design (other platforms keep the URL untouched).
"""

import pytest
from sqlalchemy.engine import make_url

from anvil.api.config import Settings, normalize_database_url
from anvil.kb.config import kb_database_url

LOCAL = "postgresql+psycopg://anvil:anvil@localhost:5433/anvil"


def host(url: str) -> str | None:
    return make_url(url).host


def test_windows_rewrites_localhost_to_ipv4_and_other_platforms_do_not(monkeypatch):
    monkeypatch.setattr("sys.platform", "win32")
    assert host(normalize_database_url(LOCAL)) == "127.0.0.1"
    assert host(normalize_database_url("postgresql://u:p@db.internal:5432/x")) == "db.internal"   # only localhost
    monkeypatch.setattr("sys.platform", "linux")
    assert normalize_database_url(LOCAL) == LOCAL


def test_settings_dsn_is_normalised_so_alembic_gets_the_ipv4_host(monkeypatch):
    monkeypatch.setattr("sys.platform", "win32")
    assert host(Settings(database_url=LOCAL).database_dsn) == "127.0.0.1"      # what migrations/env.py reads
    monkeypatch.delenv("ANVIL_DATABASE_URL", raising=False)
    assert host(Settings(database_url=None).database_dsn) == "127.0.0.1"       # the built-in default too


def test_the_knowledge_base_url_is_normalised(monkeypatch):
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setenv("ANVIL_KB_DATABASE_URL", LOCAL)
    assert host(kb_database_url()) == "127.0.0.1"
    monkeypatch.delenv("ANVIL_KB_DATABASE_URL")
    monkeypatch.setenv("ANVIL_DATABASE_URL", LOCAL)                              # falls back to the run database
    assert host(kb_database_url()) == "127.0.0.1"
    monkeypatch.setattr("sys.platform", "linux")
    assert kb_database_url() == LOCAL


@pytest.mark.parametrize("path", ["docs/SETUP.md", "scripts/start-api.ps1", ".env.example", "HANDOFF.md"])
def test_shipped_defaults_and_docs_say_127_0_0_1_not_localhost_for_postgres_and_the_gateway(path):
    from pathlib import Path

    text = (Path(__file__).resolve().parents[2] / path).read_text(encoding="utf-8")
    assert "localhost:5433" not in text and "localhost:4000" not in text, path
