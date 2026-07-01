"""C2 — pre-existing/restored SQLite DBs predate the `generator.paradigm` column;
create_all is additive-table-only and never adds columns to an existing table. Cover the
self-heal helper in isolation (no app DB) with a raw in-memory engine."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app.database import _ensure_columns


def _memory_engine():
    # StaticPool pins the whole engine to ONE underlying connection — a plain
    # sqlite:///:memory: engine hands out a fresh (and empty) in-memory DB per
    # checkout, which would make the table created below invisible to later calls.
    return create_engine(
        "sqlite:///:memory:",
        future=True,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )


def test_ensure_columns_adds_missing_paradigm_column():
    engine = _memory_engine()
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE generator (id INTEGER PRIMARY KEY, slug TEXT)")

    with engine.connect() as conn:
        cols_before = [row[1] for row in conn.exec_driver_sql("PRAGMA table_info(generator)")]
    assert "paradigm" not in cols_before

    _ensure_columns(engine)

    with engine.connect() as conn:
        cols_after = [row[1] for row in conn.exec_driver_sql("PRAGMA table_info(generator)")]
    assert "paradigm" in cols_after


def test_ensure_columns_is_idempotent():
    engine = _memory_engine()
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE generator (id INTEGER PRIMARY KEY, slug TEXT)")

    _ensure_columns(engine)
    _ensure_columns(engine)  # must not raise (e.g. "duplicate column") on the second pass

    with engine.connect() as conn:
        cols = [row[1] for row in conn.exec_driver_sql("PRAGMA table_info(generator)")]
    assert cols.count("paradigm") == 1
