from __future__ import annotations

import uuid

from app import service
from app.database import SessionLocal, init_db
from app.models import Category, Criterion, Generator, JudgeVote, ModelOutput, Task
from tests.factories import foreign_keys_suspended


def setup_module(_m):
    init_db()


def _out(db, task_id, paradigm):
    g = Generator(
        slug=f"jg-{paradigm}-{uuid.uuid4().hex}",
        name="g",
        kind="model",
        paradigm=paradigm,
    )
    db.add(g)
    db.flush()
    o = ModelOutput(task_id=task_id, generator_id=g.id, asset_path="x.glb", is_gold=False)
    db.add(o)
    db.flush()
    return g, o


def test_judge_matches_excludes_cross_paradigm():
    with SessionLocal() as db:
        cat = Category(slug=f"c{uuid.uuid4().hex}", name="c")
        db.add(cat)
        db.flush()
        crit = db.query(Criterion).filter_by(slug="overall").first() or Criterion(
            slug="overall", name="Overall"
        )
        if crit.id is None:
            db.add(crit)
            db.flush()
        t = Task(category_id=cat.id, title="t", prompt="p")
        db.add(t)
        db.flush()
        g1, o1 = _out(db, t.id, "image_recon")
        g2, o2 = _out(db, t.id, "procedural_llm")  # different paradigm
        g3, o3 = _out(db, t.id, "image_recon")  # same as g1
        for a, b in [(o1, o2), (o1, o3)]:
            db.add(
                JudgeVote(
                    task_id=t.id,
                    criterion_id=crit.id,
                    view_condition="multi4",
                    output_a_id=a.id,
                    output_b_id=b.id,
                    winner="a",
                    swap_group=uuid.uuid4().hex,
                    judge_model="claude-3-opus",
                )
            )
        db.commit()
        pairs = set(service._judge_matches_for_scope(db, crit.id, "multi4"))
        assert (g1.id, g3.id) in pairs  # within-paradigm kept
        assert (g1.id, g2.id) not in pairs and (g2.id, g1.id) not in pairs  # cross dropped


def test_judge_matches_skips_dangling_votes():
    """Dangling votes (output deleted) should be skipped gracefully, not crash."""
    with SessionLocal() as db:
        cat = Category(slug=f"c{uuid.uuid4().hex}", name="c")
        db.add(cat)
        db.flush()
        crit = db.query(Criterion).filter_by(slug="overall").first() or Criterion(
            slug="overall", name="Overall"
        )
        if crit.id is None:
            db.add(crit)
            db.flush()
        t = Task(category_id=cat.id, title="t", prompt="p")
        db.add(t)
        db.flush()
        g1, o1 = _out(db, t.id, "image_recon")
        g2, o2 = _out(db, t.id, "image_recon")

        # Create a JudgeVote with non-existent output IDs (simulating a dangling vote).
        # Enforcement refuses this now, so it is written with enforcement suspended — the
        # test's subject is a DB that already holds dangling votes.
        with foreign_keys_suspended(db):
            db.add(
                JudgeVote(
                    task_id=t.id,
                    criterion_id=crit.id,
                    view_condition="multi4",
                    output_a_id=999999998,  # does not exist
                    output_b_id=999999999,  # does not exist
                    winner="a",
                    swap_group=uuid.uuid4().hex,
                    judge_model="claude-3-opus",
                )
            )
        # Also add a valid vote
        db.add(
            JudgeVote(
                task_id=t.id,
                criterion_id=crit.id,
                view_condition="multi4",
                output_a_id=o1.id,
                output_b_id=o2.id,
                winner="a",
                swap_group=uuid.uuid4().hex,
                judge_model="claude-3-opus",
            )
        )
        db.commit()

        # Should not crash; dangling vote is skipped
        pairs = set(service._judge_matches_for_scope(db, crit.id, "multi4"))
        assert (g1.id, g2.id) in pairs  # valid vote kept
        # Note: pairs may include results from previous tests (persistent DB);
        # the key assertion is that dangling votes don't crash, and valid votes are kept
        assert len(pairs) >= 1
