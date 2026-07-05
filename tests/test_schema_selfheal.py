# tests/test_schema_selfheal.py
"""Regression coverage for app.database._ensure_columns self-healing additive columns
on pre-existing / restored SQLite DBs (create_all does not add columns to tables that
already exist). Uses a throwaway temp-file SQLite engine — never touches the app's
real engine or any study DB."""

from __future__ import annotations

import tempfile
from pathlib import Path

from sqlalchemy import create_engine

from app.database import _ensure_columns


def _make_bare_engine(tmp_path: Path):
    """A fresh SQLite DB with a minimal model_output table that predates the
    hidden_at column — simulates a DB booted before that column was added."""
    db_path = tmp_path / "selfheal.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE model_output (id INTEGER PRIMARY KEY, task_id INTEGER)")
    return engine


def test_ensure_columns_adds_hidden_at_to_preexisting_model_output_table():
    with tempfile.TemporaryDirectory() as tmp:
        engine = _make_bare_engine(Path(tmp))

        with engine.begin() as conn:
            cols_before = [
                row[1] for row in conn.exec_driver_sql("PRAGMA table_info(model_output)")
            ]
        assert "hidden_at" not in cols_before

        _ensure_columns(engine)

        with engine.begin() as conn:
            cols_after = [row[1] for row in conn.exec_driver_sql("PRAGMA table_info(model_output)")]
        assert "hidden_at" in cols_after

        engine.dispose()


def test_ensure_columns_is_model_driven_heals_voter_session_user_id():
    """Regression for the recurring drift class: a voter_session predating the user_id column
    must self-heal WITHOUT a hardcoded rule (the model-driven diff finds it). This exact column
    broke voting on a stale DB."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "drift.db"
        engine = create_engine(f"sqlite:///{db_path}", future=True)
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "CREATE TABLE voter_session (session_id VARCHAR(64) PRIMARY KEY, n_votes INTEGER)"
            )
            cols_before = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(voter_session)")}
        assert "user_id" not in cols_before

        _ensure_columns(engine)

        with engine.begin() as conn:
            cols_after = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(voter_session)")}
        assert "user_id" in cols_after
        engine.dispose()


def test_sessionlocal_self_heals_schema_once(monkeypatch):
    """A standalone script that only calls SessionLocal() (never init_db()) still gets a healed
    schema: the FIRST session triggers init_db() exactly once, later sessions don't re-run it.
    This is the causal fix for the recurring generate_*-script schema-lag bug — no per-script
    init_db() call needed."""
    import app.database as database

    calls = {"n": 0}
    real_init = database.init_db

    def spy():
        calls["n"] += 1
        real_init()

    monkeypatch.setattr(database, "init_db", spy)
    monkeypatch.setattr(database, "_schema_ready", False)

    database.SessionLocal().close()
    assert calls["n"] == 1  # first session healed the schema
    database.SessionLocal().close()
    assert calls["n"] == 1  # subsequent sessions do not re-run init_db (idempotent guard)
