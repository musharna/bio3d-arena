"""SQLAlchemy engine + session factory. SQLite for the MVP; swap DATABASE_URL for Postgres."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from . import config

config.ensure_dirs()


def engine_kwargs(url: str) -> dict:
    """create_engine args by dialect.

    SQLite (single-file dev): share the connection across FastAPI's threadpool, and wait up
    to 30s for a write lock (``timeout``→busy_timeout) instead of failing instantly — so a
    batch scorer's brief per-output write never 500s a concurrent arena request.
    Postgres/MySQL (production): a real connection pool with liveness checks so the
    app survives idle-connection drops and many concurrent workers.
    """
    if url.startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False, "timeout": 30}}
    return {
        "pool_pre_ping": True,  # validate connections before use (drop stale ones)
        "pool_size": config.DB_POOL_SIZE,
        "max_overflow": config.DB_MAX_OVERFLOW,
    }


engine = create_engine(config.DATABASE_URL, future=True, **engine_kwargs(config.DATABASE_URL))

if config.DATABASE_URL.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
        """WAL so readers never block on the single writer; NORMAL sync is durable enough
        for a dev arena and far faster; busy_timeout backstops the connect-arg timeout."""
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.close()


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


def _ensure_columns(engine) -> None:  # noqa: ANN001
    """create_all does not add columns to existing tables; add any missing additive columns
    here so pre-existing / restored SQLite DBs self-heal on boot."""

    with engine.begin() as conn:
        cols = [row[1] for row in conn.exec_driver_sql("PRAGMA table_info(generator)")]
        if "generator" and cols and "paradigm" not in cols:
            conn.exec_driver_sql("ALTER TABLE generator ADD COLUMN paradigm VARCHAR(32) DEFAULT ''")

        cols = [row[1] for row in conn.exec_driver_sql("PRAGMA table_info(model_output)")]
        if cols and "hidden_at" not in cols:
            conn.exec_driver_sql("ALTER TABLE model_output ADD COLUMN hidden_at DATETIME")


def init_db() -> None:
    """Create all tables, then self-heal any additive columns missing from a pre-existing
    (e.g. restored-from-backup) DB. Idempotent."""
    from . import models  # noqa: F401  (import registers models on Base.metadata)

    Base.metadata.create_all(bind=engine)
    _ensure_columns(engine)
