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


def _make_bare_comparison_engine(tmp_path: Path):
    """A fresh SQLite DB with a minimal comparison table that predates the
    ballot_id column — simulates a DB booted before K-wise voting was added."""
    db_path = tmp_path / "selfheal_comparison.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE comparison (id INTEGER PRIMARY KEY, task_id INTEGER)")
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


def test_ensure_columns_adds_ballot_id_to_preexisting_comparison_table():
    with tempfile.TemporaryDirectory() as tmp:
        engine = _make_bare_comparison_engine(Path(tmp))

        with engine.begin() as conn:
            cols_before = [row[1] for row in conn.exec_driver_sql("PRAGMA table_info(comparison)")]
        assert "ballot_id" not in cols_before

        _ensure_columns(engine)

        with engine.begin() as conn:
            cols_after = [row[1] for row in conn.exec_driver_sql("PRAGMA table_info(comparison)")]
        assert "ballot_id" in cols_after

        engine.dispose()
