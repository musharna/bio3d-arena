"""The orphan-JudgeVote purge must be precise: orphans go, everything else stays.

Context: SQLite does not enforce declared foreign keys unless PRAGMA foreign_keys=ON, so
deleting a ModelOutput silently orphaned the JudgeVote rows referencing it (47 such rows
found in the 2026-07-26 pre-launch audit, from the 12 cube-only outputs removed in task
#90). This purge is the remediation.

The app now sets that pragma, so this corruption can no longer originate here. The purge is
still needed and still tested, because enforcement is per-connection: a database written by
the sqlite3 CLI, by another tool, or by any build predating the change can still arrive with
dangling rows. These tests therefore manufacture the orphan deliberately, with enforcement
suspended — see tests/factories.foreign_keys_suspended.
"""

from __future__ import annotations

import itertools

from app.models import Category, Criterion, Generator, JudgeVote, ModelOutput, Task
from scripts.purge_orphan_judge_votes import orphan_judge_vote_ids, purge
from tests.factories import foreign_keys_suspended

_seq = itertools.count()


def _fixture(db):
    """Two live outputs + one that we delete out from under its judge votes.

    Slugs are made unique per call: the shared db_session can already contain
    seeded rows (a 'plants' category, an 'overall' criterion), and hardcoded
    slugs collide on category.slug/criterion.slug depending on test order —
    passing in isolation while failing the full suite.
    """
    n = next(_seq)
    cat = Category(name=f"Cat{n}", slug=f"purge-cat-{n}")
    crit = Criterion(name=f"Crit{n}", slug=f"purge-crit-{n}")
    gen = Generator(name=f"G{n}", slug=f"purge-gen-{n}", kind="model")
    db.add_all([cat, crit, gen])
    db.flush()
    task = Task(category_id=cat.id, title="T", prompt="p", criteria_note="n", active=True)
    db.add(task)
    db.flush()
    outs = []
    for i in range(3):
        o = ModelOutput(
            task_id=task.id,
            generator_id=gen.id,
            title=f"o{i}",
            asset_path=f"x/{i}.glb",
            asset_format="glb",
            meta_json="{}",
            n_comparisons=0,
            is_gold=False,
        )
        db.add(o)
        outs.append(o)
    db.flush()
    return crit, task, outs


def _jv(db, crit, task, a, b, group="g"):
    jv = JudgeVote(
        task_id=task.id,
        swap_group=group,
        output_a_id=a,
        output_b_id=b,
        criterion_id=crit.id,
        winner="a",
        judge_model="m",
        view_condition="multi4",
    )
    db.add(jv)
    db.flush()
    return jv


def test_purge_removes_only_votes_whose_output_is_gone(db_session):
    db = db_session
    crit, task, outs = _fixture(db)
    live_a, live_b, doomed = outs

    keep = _jv(db, crit, task, live_a.id, live_b.id)
    orphan_left = _jv(db, crit, task, doomed.id, live_a.id)  # missing on the A side
    orphan_right = _jv(db, crit, task, live_b.id, doomed.id)  # ...and on the B side
    db.flush()

    # Delete the output the way it happened in production: directly, with FKs unenforced.
    doomed_id = doomed.id
    with foreign_keys_suspended(db):
        db.delete(doomed)
    assert db.get(ModelOutput, doomed_id) is None

    found = set(orphan_judge_vote_ids(db))
    assert found == {orphan_left.id, orphan_right.id}, "must catch BOTH sides of the pair"

    res = purge(db, apply=True)
    assert res == {"orphans": 2, "deleted": 2}

    remaining = {jv.id for jv in db.query(JudgeVote).all()}
    assert remaining == {keep.id}


def test_purge_dry_run_deletes_nothing(db_session):
    db = db_session
    crit, task, outs = _fixture(db)
    doomed = outs[2]
    _jv(db, crit, task, doomed.id, outs[0].id)
    with foreign_keys_suspended(db):
        db.delete(doomed)

    res = purge(db, apply=False)
    assert res == {"orphans": 1, "deleted": 0}
    assert db.query(JudgeVote).count() == 1


def test_purge_is_a_noop_on_a_clean_database(db_session):
    db = db_session
    crit, task, outs = _fixture(db)
    _jv(db, crit, task, outs[0].id, outs[1].id)
    db.commit()

    assert orphan_judge_vote_ids(db) == []
    assert purge(db, apply=True) == {"orphans": 0, "deleted": 0}
    assert db.query(JudgeVote).count() == 1
