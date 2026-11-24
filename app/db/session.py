"""Engine / session helpers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.models import Base

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine(url: str | None = None) -> Engine:
    global _engine, _SessionLocal
    settings = get_settings()
    database_url = url or settings.database_url
    if _engine is not None and url is None:
        return _engine

    # Ensure parent dir exists for SQLite file URLs.
    if database_url.startswith("sqlite:///"):
        path = Path(database_url.removeprefix("sqlite:///"))
        if path.parent.as_posix() not in {"", "."}:
            path.parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(database_url, future=True)

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn: object, _connection_record: object) -> None:  # noqa: ANN001
        cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    if url is None:
        _engine = engine
        _SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return engine


def init_db(url: str | None = None) -> Engine:
    """Create all tables (dev / tests). Alembic is the production path."""
    engine = get_engine(url)
    Base.metadata.create_all(engine)
    return engine


def reset_engine() -> None:
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


@contextmanager
def session_scope(url: str | None = None) -> Iterator[Session]:
    engine = get_engine(url)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
