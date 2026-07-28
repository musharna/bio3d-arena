"""The schema self-heal is SQLite-only, and must not run against Postgres.

Found by the first real deploy, 2026-07-28: the container booted, connected to Neon, and
died in a restart loop with

    psycopg.errors.UndefinedTable: relation "sqlite_master" does not exist
    [SQL: SELECT name FROM sqlite_master WHERE type='table']

``ensure_schema()`` does two different jobs. ``create_all`` is dialect-agnostic and correct
everywhere. ``_ensure_columns`` and ``_ensure_commission_attempt_identity`` are repairs for a
problem only a single-file SQLite database has — it gets copied, restored from a snapshot, and
handed between checkouts, so it drifts behind the models, and they read ``sqlite_master`` /
``PRAGMA table_info`` to catch that up. Postgres has no such table, so the query is not merely
unnecessary there — it is a hard error on the boot path.

It went unnoticed because nothing had ever run ``ensure_schema`` against Postgres:
``scripts/import_public.py`` calls ``Base.metadata.create_all`` directly, so the import of the
release bundle succeeded against the very database the app then could not boot against.
"""

from __future__ import annotations

import sqlalchemy as sa

from app import database


def test_a_non_sqlite_engine_never_gets_the_sqlite_only_repairs():
    """The regression: ensure_schema against a Postgres engine must create tables and stop.

    A mock engine records DDL without a server, and — being connectionless — raises if anything
    tries to open a transaction against it. That is exactly what the SQLite repairs do
    (``with engine.begin()``), so this fails loudly on the pre-fix code and passes once the
    dialect gate is in place.
    """
    seen: list[str] = []

    def executor(sql, *args, **kwargs):
        seen.append(str(sql.compile(dialect=eng.dialect)) if hasattr(sql, "compile") else str(sql))

    eng = sa.create_mock_engine("postgresql+psycopg://", executor)
    database.ensure_schema(eng)

    ddl = "\n".join(seen).lower()
    assert "create table" in ddl, "create_all must still run on Postgres"
    assert "sqlite_master" not in ddl
    assert "pragma" not in ddl


def test_sqlite_still_gets_them():
    """The repairs must keep running where they are the point — a real SQLite file that is
    missing a column the models declare gets healed, not left broken."""
    eng = sa.create_engine("sqlite://", future=True)  # in-memory
    database.ensure_schema(eng)

    # model_output.license is nullable and carries no index — SQLite refuses to drop a column an
    # index depends on, so an indexed column would fail the setup rather than the assertion.
    with eng.begin() as c:
        c.exec_driver_sql("ALTER TABLE model_output DROP COLUMN license")
        cols = {r[1] for r in c.exec_driver_sql("PRAGMA table_info(model_output)")}
    assert "license" not in cols  # precondition: we really did break it

    database.ensure_schema(eng)  # second call must notice and repair

    with eng.begin() as c:
        cols = {r[1] for r in c.exec_driver_sql("PRAGMA table_info(model_output)")}
    assert "license" in cols, "the SQLite self-heal must still add a missing column"
