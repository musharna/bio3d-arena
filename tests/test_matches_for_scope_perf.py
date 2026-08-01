"""`_matches_for_scope` executed a relational projection as one round trip per vote.

Measured on the public instance 2026-07-30, `/models/{slug}` took **23 seconds** — three
consecutive warm runs, every model tried — while `/models` served in 0.83s in the same window.
Instrumented query counts on the real corpus located it exactly:

    head_to_head_record     2079 queries
    _model_cards              34 queries
    _model_share_context      34 queries

`_matches_for_scope` resolved each vote's objects with per-row `db.get` — two `ModelOutput`
rows and two `Generator` rows per vote — and `head_to_head_record` ran it TWICE over the same
scope (once for the decisive tally, once with ties). Fly (ord) → Neon (aws us-east-2) is a
~10ms round trip, so 2079 × 10ms ≈ 21s against 23s observed.

Nothing caught it because SQLite makes the same 2079 queries cost 0.64s: an in-process call
has no network cost, so the defect is invisible to a timing assertion in this suite. The guard
that *does* survive the change of database is a COUNT — the number of statements a scope costs
must not scale with the number of votes in it. That is what the first test below asserts, and
it fails on the pre-fix implementation for the stated reason (queries grow ~4 per vote).

This is the third site of this pattern; PR #110 fixed `/models` and `/coverage`. The rules
`_matches_for_scope` enforces are load-bearing for RANKINGS (it feeds the Bradley-Terry fit and
every leaderboard), so the behavioural tests here pin the ones its own callers depend on and
that the existing suite did not already cover — tie splitting, ballot-group keys, bad-vote
exclusion, trust gating and dangling outputs. Cross-paradigm, self-match and Mode-A reference
exclusion are covered by tests/test_paradigm_aggregation.py and tests/test_mode_a_scan_exclusion.py.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import event, func, select, update

from app import config, service
from app.database import SessionLocal, engine, init_db
from app.models import (
    Category,
    Comparison,
    Criterion,
    Generator,
    ModelOutput,
    Task,
    Vote,
    VoterSession,
)


def setup_module(_m):
    init_db()


@pytest.fixture
def db():
    with SessionLocal() as s:
        yield s


class QueryCounter:
    """Counts SQL statements issued on the shared engine while active."""

    def __init__(self):
        self.n = 0

    def __enter__(self):
        self._h = lambda *a, **k: setattr(self, "n", self.n + 1)
        event.listen(engine, "before_cursor_execute", self._h)
        return self

    def __exit__(self, *exc):
        event.remove(engine, "before_cursor_execute", self._h)
        return False


def _criterion(db) -> Criterion:
    crit = db.execute(select(Criterion).where(Criterion.slug == "overall")).scalars().first()
    if crit is None:
        crit = Criterion(slug="overall", name="Overall")
        db.add(crit)
        db.flush()
    return crit


def _scope(db, *, n_votes: int, winner: str = "a", paradigm: str = "image_recon"):
    """A scope with `n_votes` decisive votes, each between two DISTINCT generators.

    Distinct generators per vote on purpose: reusing two generators would let an ORM identity
    map serve every lookup from cache after the first vote, which would hide exactly the
    per-row fetching this is measuring.

    Every test scopes its assertions to the returned task's OWN category. The `overall`
    criterion is shared across the whole suite, so a criterion-only scope returns every other
    test's votes too — which silently broke the group-key assertions here on the first run.
    """
    tag = uuid.uuid4().hex[:8]
    crit = _criterion(db)
    cat = Category(slug=f"perf-{tag}", name="Perf")
    db.add(cat)
    db.flush()
    task = Task(category_id=cat.id, title=f"Perf {tag}", prompt="p")
    db.add(task)
    db.flush()
    made = []
    for i in range(n_votes):
        ga = Generator(slug=f"pa-{tag}-{i}", name=f"A{i}", kind="model", paradigm=paradigm)
        gb = Generator(slug=f"pb-{tag}-{i}", name=f"B{i}", kind="model", paradigm=paradigm)
        db.add_all([ga, gb])
        db.flush()
        oa = ModelOutput(task_id=task.id, generator_id=ga.id, asset_path=f"p/{tag}-{i}a.glb")
        ob = ModelOutput(task_id=task.id, generator_id=gb.id, asset_path=f"p/{tag}-{i}b.glb")
        db.add_all([oa, ob])
        db.flush()
        comp = Comparison(
            task_id=task.id,
            output_a_id=oa.id,
            output_b_id=ob.id,
            criterion_id=crit.id,
            session_id=f"{tag}-{i}",
            is_gold=False,
        )
        db.add(comp)
        db.flush()
        db.add(Vote(comparison_id=comp.id, winner=winner, session_id=f"{tag}-{i}"))
        db.flush()
        made.append((ga, gb, comp))
    db.commit()
    return crit, task, made


# --- the defect --------------------------------------------------------------------


def test_the_query_count_does_not_scale_with_the_number_of_votes(db):
    """The load-bearing guard, and the one that fails before the fix.

    A timing assertion cannot express this: on SQLite the per-row fetches are cheap enough to
    pass any threshold, which is precisely why 23 seconds of production latency sat behind a
    green suite. Statement COUNT is database-independent — if it grows with the votes in a
    scope, every one of those statements is a network round trip in production.
    """
    crit, small_task, _ = _scope(db, n_votes=4)
    db.expunge_all()  # an already-warm identity map would serve the lookups and hide the N+1
    with QueryCounter() as small:
        service._matches_for_scope(db, crit.id, category_ids={small_task.category_id})

    _, big_task, _ = _scope(db, n_votes=16)
    db.expunge_all()
    with QueryCounter() as large:
        service._matches_for_scope(db, crit.id, category_ids={big_task.category_id})

    assert large.n <= small.n + 2, (
        f"query count scales with votes ({small.n} -> {large.n}): each extra statement is a "
        "network round trip in production"
    )


def test_head_to_head_scans_the_scope_once_not_twice(db):
    """`head_to_head_record` called `_matches_for_scope` twice for the same scope, differing
    only in `include_ties` — doubling the cost for data derivable in a single pass."""
    crit, task, made = _scope(db, n_votes=6)
    gen_id = made[0][0].id
    scope = {task.category_id}
    db.expunge_all()
    with QueryCounter() as c:
        service.head_to_head_record(db, gen_id, "overall", category_ids=scope)
    db.expunge_all()
    with QueryCounter() as one:
        service._matches_for_scope(db, crit.id, category_ids=scope)
    assert c.n <= one.n + 4, (
        f"head_to_head_record costs {c.n} queries against {one.n} for a single scope scan — "
        "it is still scanning twice"
    )


# --- behaviour that must survive the rewrite ----------------------------------------


def test_a_tie_is_split_into_both_directions_only_when_ties_are_included(db):
    """The BT fitting device: a tie informs the fit as a win each way, so `include_ties=True`
    emits BOTH orderings and `include_ties=False` emits neither."""
    crit, task, made = _scope(db, n_votes=1, winner="tie")
    ga, gb, _ = made[0]
    scope = {task.category_id}
    with_ties, _ = service._matches_for_scope(db, crit.id, category_ids=scope, include_ties=True)
    decisive, _ = service._matches_for_scope(db, crit.id, category_ids=scope, include_ties=False)
    assert (ga.id, gb.id) in with_ties and (gb.id, ga.id) in with_ties
    assert (ga.id, gb.id) not in decisive and (gb.id, ga.id) not in decisive


def test_a_tie_carries_one_group_key_for_both_of_its_halves(db):
    """Both halves of a split tie come from ONE comparison, so bootstrap resampling must move
    them together — otherwise a tie is resampled as two independent observations."""
    crit, task, _made = _scope(db, n_votes=1, winner="tie")
    matches, groups = service._matches_for_scope(
        db, crit.id, category_ids={task.category_id}, include_ties=True
    )
    assert len(matches) == len(groups) == 2
    assert groups[0] == groups[1], "split tie must share one bootstrap group key"


def test_a_native_pairwise_vote_gets_its_own_singleton_group_key(db):
    """Pairwise votes have no ballot, so each is its own group — keyed negatively off the
    comparison id so it can never collide with a real ballot_id."""
    crit, task, made = _scope(db, n_votes=2)
    _, groups = service._matches_for_scope(db, crit.id, category_ids={task.category_id})
    assert len(set(groups)) == 2, "pairwise votes must not share a bootstrap group"
    ids = {-c.id for _, _, c in made}
    assert set(groups) <= ids, f"group keys {groups} are not the negated comparison ids {ids}"


def test_a_bad_vote_is_not_a_match(db):
    """'both bad' is a rejection of both outputs, not a preference between them."""
    crit, task, made = _scope(db, n_votes=1, winner="bad")
    ga, gb, _ = made[0]
    matches, _ = service._matches_for_scope(
        db, crit.id, category_ids={task.category_id}, include_ties=True
    )
    assert (ga.id, gb.id) not in matches and (gb.id, ga.id) not in matches


def test_a_vote_from_an_untrusted_session_is_excluded(db):
    """The anti-abuse gate: below TRUST_THRESHOLD a session's votes stop counting."""
    crit, task, made = _scope(db, n_votes=1)
    ga, gb, comp = made[0]
    scope = {task.category_id}
    db.add(VoterSession(session_id=comp.session_id, trust=config.TRUST_THRESHOLD - 0.5))
    db.commit()
    matches, _ = service._matches_for_scope(db, crit.id, category_ids=scope)
    assert (ga.id, gb.id) not in matches, "untrusted vote counted"
    # Positive control: raise the same session back above the threshold and it returns.
    vs = (
        db.execute(select(VoterSession).where(VoterSession.session_id == comp.session_id))
        .scalars()
        .one()
    )
    vs.trust = config.TRUST_THRESHOLD
    db.commit()
    matches, _ = service._matches_for_scope(db, crit.id, category_ids=scope)
    assert (ga.id, gb.id) in matches, "trusted vote missing"


def test_a_vote_whose_output_was_deleted_is_skipped_not_fatal(db):
    """Dangling comparisons exist in real databases (see the 2026-06-29 calibration sweep);
    the scope must drop them rather than raise."""
    from tests.factories import foreign_keys_suspended

    crit, task, made = _scope(db, n_votes=2)
    ga, gb, comp = made[0]
    keep_a, keep_b, _ = made[1]
    missing = db.execute(select(func.max(ModelOutput.id))).scalar() + 1000
    with foreign_keys_suspended(db):
        db.execute(update(Comparison).where(Comparison.id == comp.id).values(output_a_id=missing))
        db.commit()
    matches, groups = service._matches_for_scope(db, crit.id, category_ids={task.category_id})
    assert (ga.id, gb.id) not in matches, "dangling comparison produced a match"
    assert (keep_a.id, keep_b.id) in matches, "positive control: intact comparison survived"
    assert len(matches) == len(groups), "matches and groups must stay parallel"


def test_category_scoping_filters_to_the_given_kingdom(db):
    """`category_ids` is a kingdom; an EMPTY set must be inert, never a fallback to all."""
    crit, task, made = _scope(db, n_votes=1)
    ga, gb, _ = made[0]
    in_scope, _ = service._matches_for_scope(db, crit.id, category_ids={task.category_id})
    assert (ga.id, gb.id) in in_scope
    out_of_scope, _ = service._matches_for_scope(db, crit.id, category_ids=set())
    assert out_of_scope == [], "an empty kingdom must yield no matches, not every match"
