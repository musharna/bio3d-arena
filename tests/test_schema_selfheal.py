# tests/test_schema_selfheal.py
"""Regression coverage for app.database._ensure_columns self-healing additive columns
on pre-existing / restored SQLite DBs (create_all does not add columns to tables that
already exist). Uses a throwaway temp-file SQLite engine — never touches the app's
real engine or any study DB."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError

from app.database import _ensure_columns, ensure_schema


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


# The pre-protocol table, copied VERBATIM from the live sweep DB (data/arena-preview.db, 124 rows):
# two-column unique, no protocol column, and NOT NULL on every column the ORM gave a Python-side
# default. A hand-simplified stand-in (nullable columns) would have quietly tested a schema the
# migration never has to survive.
_LEGACY_DDL = """
CREATE TABLE commission_attempt (
    id INTEGER NOT NULL,
    task_id INTEGER NOT NULL,
    model_id VARCHAR(128) NOT NULL,
    generator_id INTEGER,
    output_id INTEGER,
    status VARCHAR(20) NOT NULL,
    error TEXT NOT NULL,
    script TEXT NOT NULL,
    mesh_stats_json TEXT NOT NULL,
    duration_ms INTEGER NOT NULL,
    created DATETIME NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_commission_attempt UNIQUE (model_id, task_id),
    FOREIGN KEY(task_id) REFERENCES task (id),
    FOREIGN KEY(generator_id) REFERENCES generator (id),
    FOREIGN KEY(output_id) REFERENCES model_output (id)
)
"""

# A raw (non-ORM) write, like the ones scripts/promote_generators.py makes: every NOT NULL column
# whose default lives in Python rather than in the DDL must be supplied by hand.
_INSERT_REPAIR = (
    "INSERT INTO commission_attempt "
    "(task_id, model_id, status, protocol, error, script, mesh_stats_json, duration_ms, created) "
    "VALUES (7, '{model}', 'ok', 'repair', '', '', '{{}}', 0, '2026-07-14')"
)


def test_stale_commission_attempt_identity_is_rebuilt_and_rows_survive():
    """A DB carrying the OLD UNIQUE(model_id, task_id) must be rebuilt to UNIQUE(model_id, task_id,
    protocol) — the one schema change _ensure_columns structurally cannot make (it only ADDs
    columns; SQLite cannot ALTER a constraint at all).

    This is the schema that killed the live smoke run: the harness generated a valid mesh, then the
    INSERT collided with the legacy row for the same pair. The rebuild must be lossless — the legacy
    rows ARE the evidence for why the protocol changed, so a migration that dropped them to make
    room would be destroying the finding."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "stale_identity.db"
        engine = create_engine(f"sqlite:///{db_path}", future=True)
        with engine.begin() as conn:
            conn.exec_driver_sql(_LEGACY_DDL)
            conn.exec_driver_sql(
                "CREATE INDEX ix_commission_attempt_model_id ON commission_attempt (model_id)"
            )
            conn.exec_driver_sql(
                "INSERT INTO commission_attempt "
                "(id, task_id, model_id, status, error, script, mesh_stats_json, duration_ms, "
                " created) "
                "VALUES (1, 7, 'x-ai/grok-4.20', 'invalid_mesh', 'Traceback', 'import bpy', "
                "'{}', 900, '2026-07-13')"
            )

        ensure_schema(engine)

        with engine.begin() as conn:
            # the legacy row survived, and self-healed to the truth about itself
            row = conn.exec_driver_sql(
                "SELECT model_id, task_id, status, protocol FROM commission_attempt"
            ).fetchall()
            assert row == [("x-ai/grok-4.20", 7, "invalid_mesh", "legacy")]

            # and the same pair is now measurable under the new protocol
            conn.exec_driver_sql(_INSERT_REPAIR.format(model="x-ai/grok-4.20"))
            assert conn.exec_driver_sql("SELECT COUNT(*) FROM commission_attempt").scalar() == 2

        engine.dispose()


def test_rebuilt_commission_attempt_still_rejects_a_duplicate_within_one_protocol():
    """The rebuild must widen identity by protocol, not abolish it — resumability depends on a pair
    being attempted at most once per protocol."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "stale_identity_dup.db"
        engine = create_engine(f"sqlite:///{db_path}", future=True)
        with engine.begin() as conn:
            conn.exec_driver_sql(_LEGACY_DDL)

        ensure_schema(engine)

        with engine.begin() as conn:
            conn.exec_driver_sql(_INSERT_REPAIR.format(model="m"))
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.exec_driver_sql(_INSERT_REPAIR.format(model="m"))
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
