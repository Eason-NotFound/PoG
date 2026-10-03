from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


def build_engine(database_url: str) -> Engine:
    if not database_url.startswith("postgresql+psycopg://"):
        raise RuntimeError("A1 requires PostgreSQL through psycopg; SQLite is unsupported")
    return create_engine(database_url, pool_pre_ping=True, future=True)


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def session_dependency(factory: sessionmaker[Session]):
    def _get_session() -> Generator[Session, None, None]:
        with factory() as session:
            yield session

    return _get_session
