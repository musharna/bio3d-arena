"""SQLite must enforce the foreign keys it already declares.

SQLite parses `REFERENCES` clauses but ignores them entirely unless `PRAGMA foreign_keys=ON`
is set — per connection, every connection. Every FK in models.py was therefore decorative:
deleting a parent silently stranded its children rather than being refused.

That is not hypothetical. It has produced orphaned rows twice: 45 orphaned `judge_vote` rows
(purged in PR #93) and 12 dangling `calibration_pair` rows (purged 2026-06-29, where the
dangling rows 500'd a live page). Both times the fix was a purge script — remediation that
removes the rows and leaves the mechanism that created them fully intact.

These tests pin the mechanism, not the symptom: a parent delete that would orphan children
must FAIL LOUD instead of succeeding quietly.
"""

from __future__ import annotations

import random

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.database import SessionLocal, engine, init_db
from app.models import Category, Criterion, Generator, JudgeVote, ModelOutput, Task


def setup_module(_m):
    init_db()


def _graph(db, tag: str):
    """Build a minimal category→task→output graph. Slugs carry a random tag so these rows
    never collide with another test's under the suite's shared engine."""
    cat = Category(slug=f"c-fk-{tag}", name="Plants")
    db.add(cat)
    db.flush()
    task = Task(category_id=cat.id, title=f"FK Task {tag}", prompt="p")
    gen = Generator(slug=f"g-fk-{tag}", name="FKGen")
    db.add_all([task, gen])
    db.flush()

    def out():
        o = ModelOutput(
            task_id=task.id, generator_id=gen.id, asset_path="seed/x.glb", asset_format="glb"
        )
        db.add(o)
        db.flush()
        return o

    return task, out


def test_sqlite_connections_enforce_foreign_keys():
    """The pragma is per-connection, so assert it on a connection the app actually hands out
    rather than on one we configure here."""
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_deleting_an_output_a_judge_vote_references_is_refused():
    """The exact shape of the PR #93 orphan bug: an output is deleted while judge_vote rows
    still point at it. Enforcement must refuse the delete.

    The positive control lives in this same test on purpose — an assertion that a delete
    fails proves nothing if deletes are broken generally, so the unreferenced output must
    still delete cleanly right beside it.
    """
    tag = f"{random.randint(0, 10**6)}"
    db = SessionLocal()
    try:
        task, out = _graph(db, tag)
        referenced, other, unreferenced = out(), out(), out()
        crit = db.query(Criterion).filter_by(slug="overall").first()
        if crit is None:
            crit = Criterion(slug="overall", name="Overall")
            db.add(crit)
            db.flush()
        db.add(
            JudgeVote(
                criterion_id=crit.id,
                view_condition="multi4",
                task_id=task.id,
                output_a_id=referenced.id,
                output_b_id=other.id,
                winner="a",
                judge_model="m",
                swap_group=f"sg-fk-{tag}",
            )
        )
        db.commit()

        # NEGATIVE: deleting the referenced parent would orphan the judge_vote row.
        with pytest.raises(IntegrityError):
            db.query(ModelOutput).filter_by(id=referenced.id).delete()
            db.commit()
        db.rollback()
        assert db.query(ModelOutput).filter_by(id=referenced.id).first() is not None

        # POSITIVE CONTROL: an output nothing references still deletes.
        db.query(ModelOutput).filter_by(id=unreferenced.id).delete()
        db.commit()
        assert db.query(ModelOutput).filter_by(id=unreferenced.id).first() is None
    finally:
        db.rollback()
        db.close()


def test_inserting_a_child_pointing_at_a_missing_parent_is_refused():
    """The other half of enforcement: a write may not invent a parent that does not exist.
    Without the pragma this INSERT succeeds and creates an orphan at birth."""
    tag = f"{random.randint(0, 10**6)}"
    db = SessionLocal()
    try:
        task, out = _graph(db, tag)
        real = out()
        crit = db.query(Criterion).filter_by(slug="overall").first()
        if crit is None:
            crit = Criterion(slug="overall", name="Overall")
            db.add(crit)
            db.flush()
        db.commit()

        missing_id = (db.query(ModelOutput.id).order_by(ModelOutput.id.desc()).first()[0]) + 10_000
        db.add(
            JudgeVote(
                criterion_id=crit.id,
                view_condition="multi4",
                task_id=task.id,
                output_a_id=real.id,
                output_b_id=missing_id,  # no such output
                winner="a",
                judge_model="m",
                swap_group=f"sg-fk-ins-{tag}",
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
    finally:
        db.rollback()
        db.close()
