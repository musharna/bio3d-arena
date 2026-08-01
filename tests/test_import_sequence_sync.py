"""`s.merge()` writes rows with EXPLICIT primary keys, which does not advance a Postgres
sequence. So after importing a public bundle every `<table>_id_seq` still sits where it was —
at 1 for a fresh database — while the imported rows occupy ids far above it.

Nothing fails at import time. It fails later, in production, when the sequence climbs into the
occupied range and `INSERT` hits a duplicate key. Measured on the live instance 2026-07-28,
after the bundle import:

    vote        sequence last_value=1    max(id)=461   -> 48 inserts to collision
    comparison  sequence last_value=14   max(id)=1404  -> 553 inserts to collision
    generator / model_output / task / rating ... last_value=0 -> collision on the NEXT insert

48 votes is a third of the way to the 167 the launch board needs, so the site would have
started 500ing on /api/vote well before it could rank anything.

SQLite has no sequences — its AUTOINCREMENT derives the next rowid from max(rowid), so it is
immune, which is exactly why the whole test suite stayed green while production was broken.
The sync is therefore Postgres-only and must be a no-op elsewhere.
"""

from __future__ import annotations

import sqlalchemy as sa

from scripts.import_public import sync_id_sequences


def test_sqlite_is_a_no_op_and_does_not_raise():
    """SQLite has no sequences at all — issuing setval there would blow up the import."""
    eng = sa.create_engine("sqlite://", future=True)
    with eng.begin() as c:
        c.execute(sa.text("create table thing (id integer primary key, v text)"))
        c.execute(sa.text("insert into thing (id, v) values (7, 'a')"))
        assert sync_id_sequences(c) == {}


def test_it_advances_a_lagging_sequence_on_postgres():
    """The real behaviour, driven through a recording connection so it can be asserted without
    a live Postgres: a sequence behind max(id) must get a setval to max(id)."""
    calls = _run_against_fake_pg(seq_last_value=1, max_id=461)
    assert calls == [("vote_id_seq", 461)], calls


def test_it_leaves_an_already_correct_sequence_alone():
    """Positive control. A sync that setval'd unconditionally would satisfy the test above
    while needlessly rewriting every sequence on every import — and would mask a real lag if
    the value it wrote were ever computed wrongly."""
    assert _run_against_fake_pg(seq_last_value=461, max_id=461) == []


def test_it_skips_an_empty_table():
    """max(id) is NULL with no rows; setval(seq, NULL) raises."""
    assert _run_against_fake_pg(seq_last_value=1, max_id=None) == []


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def scalar(self):
        return self._rows[0][0] if self._rows else None


class _FakePgConn:
    """Minimal stand-in: postgres dialect, one sequence, one table."""

    dialect = type("d", (), {"name": "postgresql"})()

    def __init__(self, seq_last_value, max_id):
        self.seq_last_value = seq_last_value
        self.max_id = max_id
        self.setvals: list[tuple[str, int]] = []

    def execute(self, stmt, params=None):
        sql = str(stmt)
        if "pg_sequences" in sql:
            row = type(
                "r",
                (),
                {
                    "sequencename": "vote_id_seq",
                    "last_value": self.seq_last_value,
                    "tbl": "vote",
                    "col": "id",
                },
            )()
            return _FakeResult([row])
        if "setval" in sql:
            self.setvals.append((params["s"], params["v"]))
            return _FakeResult([])
        if "max" in sql:
            return _FakeResult([(self.max_id,)])
        raise AssertionError(f"unexpected SQL: {sql}")


def _run_against_fake_pg(*, seq_last_value, max_id):
    conn = _FakePgConn(seq_last_value, max_id)
    sync_id_sequences(conn)
    return conn.setvals
