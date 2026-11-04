"""ANVIL-80: no intake loop without a ticket source; exponential backoff while Jira fails."""

import asyncio
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from anvil.api.app import _jira_intake_loop, create_app
from anvil.api.config import Settings
from anvil.api.models import Base
from test_api import SuccessfulExecutor


def test_no_loop_when_nothing_configured(tmp_path, monkeypatch, caplog):
    for var in ("ATLASSIAN_SITE", "ATLASSIAN_EMAIL", "ATLASSIAN_API_TOKEN", "ANVIL_TICKETS_DIR"):
        monkeypatch.delenv(var, raising=False)
    app = create_app(
        settings=Settings(demo_token=SecretStr("t"), demo_user_id="u", demo_user_email="u@x"),
        database_url=f"sqlite:///{(tmp_path / 'a.db').as_posix()}",
        executor=SuccessfulExecutor(), start_intake_loop=True)
    Base.metadata.create_all(app.state.engine)
    from fastapi.testclient import TestClient

    with caplog.at_level("INFO"), TestClient(app):
        pass
    assert app.state.intake_task is None
    assert sum("intake disabled" in r.getMessage() for r in caplog.records) == 1


def test_loop_backs_off_then_recovers(monkeypatch):
    import anvil.api.app as app_mod

    app = SimpleNamespace()
    calls = iter([ValueError("down"), ValueError("down"), None, None, ValueError("down")])
    sleeps, started = [], asyncio.Event()

    async def fake_poll(_app):
        out = next(calls)
        if out is not None:
            raise out

    real_sleep = asyncio.sleep

    async def fake_sleep(d):
        sleeps.append(d)
        if len(sleeps) >= 5:
            raise asyncio.CancelledError
        await real_sleep(0)

    monkeypatch.setattr(app_mod, "poll_jira_intake", fake_poll)
    monkeypatch.setattr(app_mod.asyncio, "sleep", fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(_jira_intake_loop(app))
    # 60*2, 60*4 after two failures; 60 again after recovery; next failure starts at 120.
    assert sleeps == [120.0, 240.0, 60.0, 60.0, 120.0]


def test_loop_starts_with_only_a_tickets_dir(tmp_path, monkeypatch):
    for var in ("ATLASSIAN_SITE", "ATLASSIAN_EMAIL", "ATLASSIAN_API_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    tickets = tmp_path / "tickets"
    tickets.mkdir()
    app = create_app(
        settings=Settings(demo_token=SecretStr("t"), demo_user_id="u", demo_user_email="u@x"),
        database_url=f"sqlite:///{(tmp_path / 'b.db').as_posix()}",
        executor=SuccessfulExecutor(), start_intake_loop=True, tickets_dir=str(tickets))
    Base.metadata.create_all(app.state.engine)
    from fastapi.testclient import TestClient

    with TestClient(app):
        assert app.state.intake_task is not None
