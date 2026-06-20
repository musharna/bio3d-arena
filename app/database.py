"""SQLAlchemy engine + session factory. SQLite for the MVP; swap DATABASE_URL for Postgres."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from . import config

config.ensure_dirs()

# check_same_thread=False so the SQLite connection can be shared across FastAPI's
# threadpool workers. For Postgres this connect_arg is simply ignored.
_connect_args = (
    {"check_same_thread": False} if config.DATABASE_URL.startswith("sqlite") else {}
)
engine = create_engine(config.DATABASE_URL, connect_args=_connect_args, future=True)
SessionLocal = sessionmaker(
    bind=engine, autoflush=False, expire_on_commit=False, future=True
)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a scoped session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create all tables. Idempotent."""
    from . import models  # noqa: F401  (import registers models on Base.metadata)

    Base.metadata.create_all(bind=engine)
