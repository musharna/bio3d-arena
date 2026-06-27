from __future__ import annotations

import uuid

from app.database import SessionLocal, init_db
from app.models import (
    CalibrationPair,
    Category,
    Criterion,
    Generator,
    JudgeRating,
    JudgeVote,
    ModelOutput,
    Task,
)


def setup_module(_m):
    init_db()  # create_all picks up the new tables


def _scaffold(db):
    # unique tag per call to avoid UNIQUE constraint collisions across tests
    tag = uuid.uuid4().hex[:8]
    cat = Category(slug=f"jm-cat-{tag}", name="JM")
    db.add(cat)
    db.flush()
    crit = Criterion(slug=f"jm-overall-{tag}", name="Overall")
    gen = Generator(slug=f"jm-gen-{tag}", name="G")
    task = Task(category_id=cat.id, title="jm-task", prompt="p")
    db.add_all([crit, gen, task])
    db.flush()
    oa = ModelOutput(task_id=task.id, generator_id=gen.id, asset_path="seed/a.glb")
    ob = ModelOutput(task_id=task.id, generator_id=gen.id, asset_path="seed/b.glb")
    db.add_all([oa, ob])
    db.flush()
    return cat, crit, gen, task, oa, ob


def test_judge_vote_persists():
    with SessionLocal() as db:
        _cat, crit, _gen, task, oa, ob = _scaffold(db)
        jv = JudgeVote(
            task_id=task.id,
            output_a_id=oa.id,
            output_b_id=ob.id,
            criterion_id=crit.id,
            winner="a",
            view_condition="multi4",
            judge_model="claude-sonnet-4-6",
            swap_group="grp-1",
            rationale="A is cleaner.",
        )
        db.add(jv)
        db.commit()
        got = db.get(JudgeVote, jv.id)
        assert got.winner == "a"
        assert got.view_condition == "multi4"
        assert got.judge_model == "claude-sonnet-4-6"


def test_judge_rating_scope_is_unique_per_view_condition():
    with SessionLocal() as db:
        _cat, crit, gen, _task, _oa, _ob = _scaffold(db)
        r1 = JudgeRating(generator_id=gen.id, criterion_id=crit.id, view_condition="multi4")
        r2 = JudgeRating(generator_id=gen.id, criterion_id=crit.id, view_condition="single")
        db.add_all([r1, r2])
        db.commit()  # same gen/crit, different view_condition → allowed
        assert r1.id != r2.id


def test_calibration_pair_persists():
    with SessionLocal() as db:
        _cat, crit, _gen, task, oa, ob = _scaffold(db)
        cp = CalibrationPair(
            task_id=task.id, output_a_id=oa.id, output_b_id=ob.id, criterion_id=crit.id
        )
        db.add(cp)
        db.commit()
        assert db.get(CalibrationPair, cp.id) is not None
