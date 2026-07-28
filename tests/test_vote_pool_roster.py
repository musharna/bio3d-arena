"""Human votes are a scarce budget; the arena roster is scoped so they can reach firmness.

Measured on the live public instance 2026-07-28, at the scope the launch board actually uses
(criterion 'overall', category_id IS NULL):

    everything            53 entrants   1110 games   555 votes to firm
    commercial models     19 entrants    334 games   167 votes to firm

Nothing was firm (0/53). A pairwise vote credits n_games to BOTH entrants, so V votes buy 2V
games — the arithmetic is not close to satisfiable at launch traffic with 53 entrants, and a
board where every row reads "provisional" ranks nothing.

So the HUMAN vote pool is scoped to the commercial-model paradigms (image_recon, text_native)
via `config.ARENA_VOTE_PARADIGMS`. The LLM-authored paradigms (procedural_llm, agentic) are
NOT hidden: they keep their outputs, their pages, and their boards — including the VLM-judge
boards, which don't spend human attention and rank them independently. This is deliberately a
DIFFERENT axis from `APP_HIDDEN_PARADIGMS`, which removes a paradigm from the whole UI.

The cut is by paradigm, not by vote count. The game-count distribution runs 20 down to 0 with
no natural cliff, so "keep the top N by games" would be selecting on the dependent variable —
picking winners by how often the matchmaker happened to serve them.
"""

from __future__ import annotations

import uuid

import pytest

from sqlalchemy import select

from app import config, matchmaking, service
from app.database import SessionLocal, init_db
from app.models import Category, Generator, ModelOutput, Task


def setup_module(_m):
    init_db()


def _gen(db, paradigm):
    g = Generator(
        slug=f"g-{uuid.uuid4().hex[:10]}", name=f"G {paradigm}", kind="model", paradigm=paradigm
    )
    db.add(g)
    db.flush()
    return g


# --- the roster itself ---------------------------------------------------------------


def test_the_vote_roster_is_the_two_commercial_model_paradigms():
    assert config.ARENA_VOTE_PARADIGMS == frozenset({"image_recon", "text_native"})


def test_the_roster_does_not_overlap_the_app_hidden_paradigms():
    """A paradigm cannot be both votable and hidden from the whole UI — that would put an
    invisible model in the vote pool."""
    assert not (config.ARENA_VOTE_PARADIGMS & config.APP_HIDDEN_PARADIGMS)


# --- the exclusion set ---------------------------------------------------------------


def test_off_roster_paradigms_are_excluded_from_the_vote_pool():
    with SessionLocal() as db:
        keep = _gen(db, "image_recon")
        keep2 = _gen(db, "text_native")
        off = _gen(db, "procedural_llm")
        off2 = _gen(db, "agentic")
        excluded = service.vote_pool_excluded_generator_ids(db)
        assert off.id in excluded and off2.id in excluded
        assert keep.id not in excluded and keep2.id not in excluded
        db.rollback()


def test_a_generator_with_no_paradigm_is_off_roster():
    """`paradigm` is nullable and SQL `NOT IN` never matches NULL, so a null-paradigm
    generator would silently slip INTO the pool under a naive notin_() filter."""
    with SessionLocal() as db:
        nul = _gen(db, None)
        assert nul.id in service.vote_pool_excluded_generator_ids(db)
        db.rollback()


def test_an_empty_roster_scopes_nothing(monkeypatch):
    """Positive control on the mechanism's off-switch: emptying the allowlist must restore the
    unscoped pool, not exclude everything. Without this, a bug that returned every generator
    would look identical to a correctly-scoped pool in the tests above."""
    monkeypatch.setattr(config, "ARENA_VOTE_PARADIGMS", frozenset())
    with SessionLocal() as db:
        off = _gen(db, "procedural_llm")
        assert service.vote_pool_excluded_generator_ids(db) == set()
        assert off.id not in service.vote_pool_excluded_generator_ids(db)
        db.rollback()


# --- scoped OUT of voting, but NOT hidden -------------------------------------------


def test_off_roster_paradigms_stay_visible_on_the_boards():
    """The whole point of a separate axis. If these landed in app_hidden_generator_ids they
    would vanish from /leaderboard, /models and the judge boards too — which is the thing this
    change is explicitly NOT doing."""
    with SessionLocal() as db:
        off = _gen(db, "procedural_llm")
        off2 = _gen(db, "agentic")
        assert off.id not in service.app_hidden_generator_ids(db)
        assert off2.id not in service.app_hidden_generator_ids(db)
        assert off.id not in service.mode_a_excluded_generator_ids(db)
        assert off2.id not in service.mode_a_excluded_generator_ids(db)
        db.rollback()


# --- the composed pool predicate ----------------------------------------------------


def _task_with(db, *generators):
    """A task carrying one output per generator, PERSISTED.

    Flushing matters: the pool predicate keys off `ModelOutput.generator_id`, which stays None
    on an un-flushed in-memory object. An unpersisted fixture sails through every
    generator-scoped exclusion, so the test would pass for the wrong reason — that is exactly
    how the first draft of these tests failed.
    """
    cat = db.execute(select(Category)).scalars().first()
    if cat is None:
        cat = Category(slug=f"c-{uuid.uuid4().hex[:8]}", name="C")
        db.add(cat)
        db.flush()
    t = Task(title="t", prompt="p", category_id=cat.id)
    t.outputs = [
        ModelOutput(generator=g, n_comparisons=0, asset_path="x.glb", is_gold=False)
        for g in generators
    ]
    db.add(t)
    db.flush()
    assert all(o.generator_id is not None for o in t.outputs), "fixture not persisted"
    return t


def test_pick_pair_serves_the_roster_and_not_the_rest():
    from app.main import _vote_pool_predicate

    with SessionLocal() as db:
        a, b = _gen(db, "image_recon"), _gen(db, "image_recon")
        c, d = _gen(db, "agentic"), _gen(db, "agentic")
        task = _task_with(db, a, b, c, d)
        excluded = _vote_pool_predicate(db)
        for _ in range(40):
            pair = matchmaking.pick_pair(None, task, exclude_fn=excluded)
            assert pair is not None, "the on-roster pair must still be servable"
            assert {p.generator_id for p in pair} == {a.id, b.id}
        db.rollback()


def test_a_task_with_only_off_roster_outputs_yields_no_pair():
    from app.main import _vote_pool_predicate

    with SessionLocal() as db:
        c, d = _gen(db, "procedural_llm"), _gen(db, "procedural_llm")
        task = _task_with(db, c, d)
        assert matchmaking.pick_pair(None, task, exclude_fn=_vote_pool_predicate(db)) is None
        db.rollback()


def test_the_same_task_IS_pairable_once_the_roster_is_opened(monkeypatch):
    """Positive control for the test above: proves the None came from the roster scope and not
    from some unrelated reason the fixture happens to trip (unpairable task, gated output).
    Same task, same predicate factory, only the allowlist differs."""
    from app.main import _vote_pool_predicate

    with SessionLocal() as db:
        c, d = _gen(db, "procedural_llm"), _gen(db, "procedural_llm")
        task = _task_with(db, c, d)
        monkeypatch.setattr(config, "ARENA_VOTE_PARADIGMS", frozenset())
        pair = matchmaking.pick_pair(None, task, exclude_fn=_vote_pool_predicate(db))
        assert pair is not None
        assert {p.generator_id for p in pair} == {c.id, d.id}
        db.rollback()


@pytest.mark.parametrize("builder", ["_build_comparison", "_build_kwise_comparison"])
def test_both_arena_builders_use_the_one_predicate(builder):
    """Pairwise and k-wise each had their OWN copy of the exclusion closure. A past /api/next
    404 came from exactly that kind of drift (pick_task vs pick_pair disagreeing), so the two
    builders must share one definition rather than two that happen to match today."""
    import inspect

    from app import main

    src = inspect.getsource(getattr(main, builder))
    assert "_vote_pool_predicate(db)" in src, f"{builder} does not use the shared predicate"
    assert "def _vote_excluded" not in src, f"{builder} still defines its own copy"


# --- the silent-dead-end guard ------------------------------------------------------


def test_ingesting_a_generator_with_no_paradigm_warns(caplog):
    """An allowlist makes NULL paradigm consequential: `upsert_generator` used to create
    generators with no paradigm, which now means ingested-and-displayed but NEVER servable for
    voting. Silent dead ends are the failure mode worth shouting about."""
    from app import ingest

    with SessionLocal() as db:
        with caplog.at_level("WARNING"):
            ingest.upsert_generator(db, f"nul-{uuid.uuid4().hex[:8]}")
        assert "off the arena vote roster" in caplog.text.lower()
        db.rollback()


def test_ingesting_a_generator_with_a_roster_paradigm_is_quiet(caplog):
    """Positive control: the warning must fire on the dead-end case only, not on every
    ingest — otherwise it is noise and gets tuned out."""
    from app import ingest

    with SessionLocal() as db:
        with caplog.at_level("WARNING"):
            g = ingest.upsert_generator(db, f"ok-{uuid.uuid4().hex[:8]}", paradigm="image_recon")
        assert "off the arena vote roster" not in caplog.text.lower()
        assert g.paradigm == "image_recon"
        assert g.id not in service.vote_pool_excluded_generator_ids(db)
        db.rollback()
