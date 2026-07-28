"""The leaderboard's cost must not scale with the number of votes.

Measured on the live public instance (2026-07-28): `/leaderboard` took ~12s on EVERY hit, with
the rating caches populated, and issued **1103 SQL statements** — 965 of them a single-row
`SELECT ... FROM model_output WHERE id = ?`. A stack trace attributed them to two loops that
resolve each comparison's two outputs one primary key at a time: `compute_bias` and
`generator_trend_series`.

This was invisible for the project's whole life because the internal instance runs SQLite
in-process, where a primary-key lookup is microseconds. Against managed Postgres each one is a
network round trip (~15-30ms app-in-ord to database-in-us-east-2), so the page cost became
`votes × 2 × RTT` — and it grows with every vote the arena collects, which is the one number
this project is trying to increase.

These tests pin the shape rather than a wall-clock time: query count must stay flat as the
number of comparisons grows. They fail on the pre-fix code because the count tracks the data.
"""

from __future__ import annotations

from sqlalchemy import event

from app import service
from app.models import Category, Comparison, Criterion, Generator, ModelOutput, Task, Vote


class _Counter:
    """Count statements against a session's bind, scoped to a with-block."""

    def __init__(self, db):
        self.engine = db.get_bind()
        self.n = 0

    def _hook(self, conn, cursor, statement, params, context, executemany):  # noqa: ANN001, ARG002
        if "model_output" in statement:
            self.n += 1

    def __enter__(self):
        event.listen(self.engine, "before_cursor_execute", self._hook)
        return self

    def __exit__(self, *exc):
        event.remove(self.engine, "before_cursor_execute", self._hook)
        return False


def _seed(db, n_comparisons: int) -> int:
    """n distinct comparisons, each with its own pair of outputs and one vote.

    Returns the criterion id the callers pass on.
    """
    import sqlalchemy as sa

    # Reuse whatever the shared suite engine already holds — slugs are UNIQUE and a prior
    # module's seeded rows are not rolled back.
    crit = db.execute(sa.select(Criterion).where(Criterion.slug == "overall")).scalars().first()
    if crit is None:
        crit = Criterion(slug="overall", name="Overall")
        db.add(crit)
    cat = db.execute(sa.select(Category).where(Category.slug == "qc-cat")).scalars().first()
    if cat is None:
        cat = Category(slug="qc-cat", name="QC")
        db.add(cat)
    db.flush()
    g1 = Generator(slug=f"qc-gen-a-{n_comparisons}", name="A", kind="model", paradigm="text_native")
    g2 = Generator(slug=f"qc-gen-b-{n_comparisons}", name="B", kind="model", paradigm="text_native")
    db.add_all([g1, g2])
    db.flush()
    t = Task(category_id=cat.id, title=f"qc-task-{n_comparisons}", prompt="p", active=True)
    db.add(t)
    db.flush()
    for i in range(n_comparisons):
        oa = ModelOutput(
            task_id=t.id, generator_id=g1.id, asset_path=f"qc/{n_comparisons}/{i}a.glb"
        )
        ob = ModelOutput(
            task_id=t.id, generator_id=g2.id, asset_path=f"qc/{n_comparisons}/{i}b.glb"
        )
        db.add_all([oa, ob])
        db.flush()
        c = Comparison(
            task_id=t.id,
            output_a_id=oa.id,
            output_b_id=ob.id,
            criterion_id=crit.id,
            session_id=f"qc-{n_comparisons}-{i}",
        )
        db.add(c)
        db.flush()
        db.add(Vote(comparison_id=c.id, winner="a", session_id=f"qc-{n_comparisons}-{i}"))
    db.flush()
    return crit.id


def test_compute_bias_does_not_query_per_comparison(db_session):
    _seed(db_session, 4)
    with _Counter(db_session) as small:
        service.compute_bias(db_session)

    _seed(db_session, 20)
    with _Counter(db_session) as big:
        service.compute_bias(db_session)

    assert big.n <= small.n + 2, (
        f"compute_bias issues per-comparison model_output queries: {small.n} -> {big.n} "
        "as comparisons grew 4 -> 24. Each one is a network round trip in production."
    )


def test_generator_trend_series_does_not_query_per_comparison(db_session):
    crit_id = _seed(db_session, 4)
    with _Counter(db_session) as small:
        service.generator_trend_series(db_session, crit_id)

    _seed(db_session, 20)
    with _Counter(db_session) as big:
        service.generator_trend_series(db_session, crit_id)

    assert big.n <= small.n + 2, (
        f"generator_trend_series issues per-comparison model_output queries: {small.n} -> {big.n} "
        "as comparisons grew 4 -> 24. Each one is a network round trip in production."
    )
