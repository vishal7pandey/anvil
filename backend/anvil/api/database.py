from collections.abc import Generator
from typing import Any

from fastapi import Request
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session as DBSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

SessionFactory = sessionmaker[DBSession]


def create_session_factory(
    database_url: str,
) -> tuple[Engine, SessionFactory]:
    url = make_url(database_url)
    engine_options: dict[str, Any] = {"pool_pre_ping": True}
    if url.get_backend_name() == "sqlite":
        engine_options["connect_args"] = {"check_same_thread": False}
        if url.database in (None, "", ":memory:"):
            engine_options["poolclass"] = StaticPool
    engine = create_engine(database_url, **engine_options)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def get_db(request: Request) -> Generator[DBSession, None, None]:
    session_factory = request.app.state.session_factory
    with session_factory() as db:
        yield db
