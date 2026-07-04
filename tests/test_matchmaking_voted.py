"""Matchmaking must exclude pairings the session has already voted on. The /api/vote
endpoint rejects a re-vote of the same (unordered) pairing with 409; if pick_pair /
pick_task ignore vote history they keep re-serving voted pairings, dead-ending a session
on 'You have already voted on this pairing' instead of ending cleanly (404) or serving a
fresh pair."""

from __future__ import annotations

import uuid

from app import integrity, matchmaking
from app.database import SessionLocal, init_db
from app.models import (
    Category,
    Comparison,
    Criterion,
    Generator,
    ModelOutput,
    Task,
    Vote,
)


def setup_module(_m):
    init_db()


# ---- unit: pick_pair vote-history exclusion (in-memory, db unused) ----


def _out(oid, paradigm="procedural_llm", n=0):
    g = Generator(slug=f"g{uuid.uuid4().hex}", name="g", kind="model", paradigm=paradigm)
    o = ModelOutput(generator=g, n_comparisons=n, asset_path="x.glb", is_gold=False)
    o.id = oid
    return o


def test_pick_pair_skips_session_voted_pair():
    # 3 same-paradigm outputs -> 3 possible pairs. With pair {1,2} voted, pick_pair must
    # return one of the other two pairs, never {1,2}.
    task = Task(title="t", prompt="p", category_id=1)
    task.outputs = [_out(1), _out(2), _out(3)]
    voted = {frozenset((1, 2))}
    for _ in range(50):
        pair = matchmaking.pick_pair(None, task, voted_pairs=voted)
        assert pair is not None
        assert frozenset((pair[0].id, pair[1].id)) != frozenset((1, 2))


def test_pick_pair_none_when_only_pair_is_voted():
    task = Task(title="t", prompt="p", category_id=1)
    task.outputs = [_out(1), _out(2)]
    voted = {frozenset((1, 2))}
    assert matchmaking.pick_pair(None, task, voted_pairs=voted) is None


def test_pick_pair_unchanged_when_no_voted_pairs():
    # Backward compat: empty/None voted_pairs behaves exactly as before.
    task = Task(title="t", prompt="p", category_id=1)
    task.outputs = [_out(1), _out(2)]
    pair = matchmaking.pick_pair(None, task)
    assert pair is not None
    assert {pair[0].id, pair[1].id} == {1, 2}


# ---- db: integrity.voted_pairs_for + pick_task exhaustion ----


def _seed_voted(db):
    """One task with exactly one votable pair; the session votes on it. Returns
    (session_id, criterion, task, {a_id, b_id})."""
    cat = Category(slug=f"mmv-{uuid.uuid4().hex[:8]}", name="C")
    db.add(cat)
    db.flush()
    crit = Criterion(slug=f"mmv-crit-{uuid.uuid4().hex[:8]}", name="Overall")
    db.add(crit)
    db.flush()
    task = Task(category_id=cat.id, title=f"mmv-{uuid.uuid4().hex[:8]}", prompt="p")
    db.add(task)
    db.flush()
    outs = []
    for i in range(2):
        g = Generator(slug=f"mmv-g{uuid.uuid4().hex}", name="g", kind="model", paradigm="p")
        db.add(g)
        db.flush()
        o = ModelOutput(
            task_id=task.id, generator_id=g.id, asset_path=f"mmv/{i}.glb", asset_format="glb"
        )
        db.add(o)
        db.flush()
        outs.append(o)
    sid = uuid.uuid4().hex
    comp = Comparison(
        task_id=task.id,
        output_a_id=outs[0].id,
        output_b_id=outs[1].id,
        criterion_id=crit.id,
        session_id=sid,
    )
    db.add(comp)
    db.flush()
    db.add(Vote(comparison_id=comp.id, winner="a", session_id=sid))
    db.commit()
    return sid, crit, task, {outs[0].id, outs[1].id}


def test_voted_pairs_for_returns_the_pair():
    with SessionLocal() as db:
        sid, crit, task, pair_ids = _seed_voted(db)
        got = integrity.voted_pairs_for(db, sid, crit.id)
        assert frozenset(pair_ids) in got


def test_pick_task_excludes_session_exhausted_task():
    with SessionLocal() as db:
        sid, crit, task, pair_ids = _seed_voted(db)
        voted = integrity.voted_pairs_for(db, sid, crit.id)
        # The task's only pair is voted -> it must not be offered to this session.
        for _ in range(20):
            picked = matchmaking.pick_task(db, category_id=task.category_id, voted_pairs=voted)
            assert picked is None or picked.id != task.id
