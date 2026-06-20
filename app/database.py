"""SQLAlchemy engine + session factory. SQLite for the MVP; swap DATABASE_URL for Postgres."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from . import config

config.ensure_dirs()


def engine_kwargs(url: str) -> dict:
    """create_engine args by dialect.

    SQLite (single-file dev): share the connection across FastAPI's threadpool.
    Postgres/MySQL (production): a real connection pool with liveness checks so the
    app survives idle-connection drops and many concurrent workers.
    """
    if url.startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False}}
    return {
        "pool_pre_ping": True,  # validate connections before use (drop stale ones)
        "pool_size": config.DB_POOL_SIZE,
        "max_overflow": config.DB_MAX_OVERFLOW,
    }


engine = create_engine(config.DATABASE_URL, future=True, **engine_kwargs(config.DATABASE_URL))
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


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
