"""/coverage and /models must not cost one round trip per output.

Measured on the live instance 2026-07-28, with a stack trace attributing every statement:

    /models    7.95s   665 queries — 406 single-row SELECT ... FROM generator WHERE id = ?
    /coverage  7.12s   569 queries — 352 of the same

352 of them came from ONE line, `coverage_summary`'s "count outputs by paradigm" loop, which
fetched every non-gold ModelOutput row and then re-fetched that row's Generator by primary key
to read a single column. It is a GROUP BY written as a Python loop. `/models` is slow for the
same reason: it calls `coverage_summary` too.

Same root cause as the `/leaderboard` fix (see test_leaderboard_query_count.py): row-at-a-time
access that is microseconds against in-process SQLite and a network round trip against managed
Postgres. It stayed invisible because the test suite and the internal instance both run SQLite.

These tests pin the SHAPE — query count must stay flat as outputs grow — rather than a
wall-clock time, which would be flaky and would not fail on the actual defect.
"""

from __future__ import annotations

from sqlalchemy import event

from app import service
from app.models import Category, Generator, ModelOutput, Task


class _Counter:
    def __init__(self, db, table: str):
        self.engine = db.get_bind()
        self.table = table
        self.n = 0

    def _hook(self, conn, cursor, statement, params, context, executemany):  # noqa: ANN001, ARG002
        if self.table in statement:
            self.n += 1

    def __enter__(self):
        event.listen(self.engine, "before_cursor_execute", self._hook)
        return self

    def __exit__(self, *exc):
        event.remove(self.engine, "before_cursor_execute", self._hook)
        return False


def _seed_outputs(db, n: int, tag: str) -> None:
    """n non-gold outputs spread over 2 generators of different paradigms."""
    import sqlalchemy as sa

    cat = db.execute(sa.select(Category).where(Category.slug == "cq-cat")).scalars().first()
    if cat is None:
        cat = Category(slug="cq-cat", name="CQ")
        db.add(cat)
        db.flush()
    g1 = Generator(slug=f"cq-a-{tag}", name="A", kind="model", paradigm="image_recon")
    g2 = Generator(slug=f"cq-b-{tag}", name="B", kind="model", paradigm="text_native")
    db.add_all([g1, g2])
    db.flush()
    t = Task(category_id=cat.id, title=f"cq-task-{tag}", prompt="p", active=True)
    db.add(t)
    db.flush()
    for i in range(n):
        db.add(
            ModelOutput(
                task_id=t.id,
                generator_id=(g1.id if i % 2 else g2.id),
                asset_path=f"cq/{tag}/{i}.glb",
                is_gold=False,
            )
        )
    db.flush()


def test_coverage_summary_does_not_query_generator_per_output(db_session):
    _seed_outputs(db_session, 4, "small")
    with _Counter(db_session, "generator") as small:
        service.coverage_summary(db_session)

    _seed_outputs(db_session, 40, "big")
    with _Counter(db_session, "generator") as big:
        service.coverage_summary(db_session)

    # 40 more outputs must not buy 40 more generator queries. Allow a small constant for the
    # extra generator/task rows the second seed itself adds.
    assert big.n <= small.n + 6, (
        f"coverage_summary issues per-output generator queries: {small.n} -> {big.n} as "
        "outputs grew 4 -> 44. Each one is a network round trip in production."
    )


def test_by_paradigm_tally_is_still_correct(db_session):
    """Positive control: the fast path must produce the SAME counts. A query-count test alone
    would pass if the tally were replaced by something that returned nothing at all."""
    _seed_outputs(db_session, 10, "correct")  # 5 image_recon + 5 text_native
    summary = service.coverage_summary(db_session)
    by = summary["by_paradigm"]
    assert by.get("image_recon", 0) >= 5, by
    assert by.get("text_native", 0) >= 5, by
    # Counts are of non-gold outputs, so the tally total must match the non-gold row count.
    import sqlalchemy as sa

    non_gold = db_session.execute(
        sa.select(sa.func.count(ModelOutput.id)).where(ModelOutput.is_gold.is_(False))
    ).scalar_one()
    assert sum(by.values()) == non_gold, (by, non_gold)


def test_a_gold_only_metric_does_not_light_up_has_mode_b(db_session):
    """The per-task loop scoped Metric/TraitScore to the task's NON-GOLD outputs. Collapsing
    those into grouped joins drops that scope unless it is carried explicitly — and then a task
    whose only measured output is a gold reference would advertise Mode-B coverage it does not
    have. Caught while writing the grouped version; pinned so it cannot come back."""
    import sqlalchemy as sa

    from app.models import Metric

    _seed_outputs(db_session, 2, "goldmetric")
    t = (
        db_session.execute(sa.select(Task).where(Task.title == "cq-task-goldmetric"))
        .scalars()
        .one()
    )
    g = (
        db_session.execute(sa.select(Generator).where(Generator.slug == "cq-a-goldmetric"))
        .scalars()
        .one()
    )

    def _has_mode_b() -> bool:
        rows = service.coverage_summary(db_session)["tasks"]
        return next(r["has_mode_b"] for r in rows if r["task"] == t.title)

    assert _has_mode_b() is False, "no metrics seeded yet"

    gold = ModelOutput(
        task_id=t.id, generator_id=g.id, asset_path="cq/goldmetric/g.glb", is_gold=True
    )
    db_session.add(gold)
    db_session.flush()
    db_session.add(Metric(output_id=gold.id, chamfer=0.1))
    db_session.flush()
    assert _has_mode_b() is False, "a GOLD output's metric advertised Mode-B coverage"


def test_gold_outputs_stay_out_of_the_tally(db_session):
    """The original loop filtered `is_gold == False`; an aggregate that forgot the predicate
    would inflate every paradigm count and no query-count test would notice."""
    import sqlalchemy as sa

    _seed_outputs(db_session, 2, "gold")
    before = sum(service.coverage_summary(db_session)["by_paradigm"].values())
    g = (
        db_session.execute(sa.select(Generator).where(Generator.slug == "cq-a-gold"))
        .scalars()
        .one()
    )
    t = db_session.execute(sa.select(Task).where(Task.title == "cq-task-gold")).scalars().one()
    db_session.add(
        ModelOutput(task_id=t.id, generator_id=g.id, asset_path="cq/gold/x.glb", is_gold=True)
    )
    db_session.flush()
    after = sum(service.coverage_summary(db_session)["by_paradigm"].values())
    assert after == before, "a gold output leaked into the paradigm tally"
