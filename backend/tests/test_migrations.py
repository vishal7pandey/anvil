"""The Control API's tables exist only as Alembic migrations (ANVIL-97).

A fresh laptop has an empty database; nothing but `alembic upgrade head` creates `users`, `sessions`, `runs`, ...
Without it the API dies at startup with 'relation "users" does not exist'. scripts/start-api.ps1 runs the migrations;
this proves they build the whole schema from nothing and that running them again is harmless.

Live (skipped without ANVIL_TEST_DATABASE_URL, which CI sets). Alembic runs in a subprocess, as the script does:
its env.py calls logging.config.fileConfig, which would otherwise reconfigure logging for the whole test process.
"""

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

BACKEND = Path(__file__).resolve().parents[1]
URL = os.environ.get("ANVIL_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(not URL, reason="ANVIL_TEST_DATABASE_URL not set")


def _upgrade(database_url: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "ANVIL_DATABASE_URL": database_url}
    return subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=180,
    )


def test_migrations_build_the_api_schema_on_an_empty_database_and_are_idempotent():
    base = make_url(URL)
    name = f"anvil_mig_{uuid.uuid4().hex[:8]}"
    admin = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        fresh = base.set(database=name).render_as_string(hide_password=False)
        first = _upgrade(fresh)
        assert first.returncode == 0, first.stderr[-1500:]
        second = _upgrade(fresh)          # already at head: a no-op, as on every restart of start-api.ps1
        assert second.returncode == 0, second.stderr[-1500:]

        engine = create_engine(fresh)
        try:
            tables = set(inspect(engine).get_table_names())
        finally:
            engine.dispose()
        assert {"users", "sessions", "runs", "events", "approvals", "alembic_version"} <= tables
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()
