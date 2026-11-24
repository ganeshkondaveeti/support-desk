"""FastAPI dependencies: DB session."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import get_engine


def get_db() -> Iterator[Session]:
    """Yield a request-scoped SQLAlchemy session (commit on success)."""
    engine = get_engine()
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


DbSession = Annotated[Session, Depends(get_db)]
