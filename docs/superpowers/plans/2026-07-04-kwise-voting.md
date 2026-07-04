<!-- ROOT_CAUSE_OK: implementation plan, not a bug fix -->

# K-wise (K=4) Voting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a simultaneous 4-up "pick the best" ballot to the arena so one voter action yields 3 within-paradigm pairwise relations, reusing the existing Elo/Bradley-Terry pipeline unchanged.

**Architecture:** A collection-and-presentation layer. A `KBallot` records what was shown; on "pick best" it expands into 3 ordinary `Comparison`+`Vote` rows (best beats each other), stamped with a shared `ballot_id`, that flow through `service.apply_vote` → Elo + BT exactly like native pairwise votes. The only ranking-engine touch is a **ballot-level bootstrap** so the correlated derived pairs don't fake-tighten confidence intervals.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 (Mapped/mapped_column), Pydantic v2, vanilla JS arena, pytest.

## Global Constraints

- Reuse the existing pipeline — NO new ranking model; derived pairs go through `service.apply_vote` + `ranking.bradley_terry`.
- Bradley-Terry is computed WITHIN each paradigm group. The 4 shown MUST be 4 distinct generators' outputs for ONE task, ONE paradigm.
- The 4 must come from the ADMITTED pool — apply the same `_vote_excluded` predicate `_build_comparison` uses (`admissibility.non_admitted_output_ids` ∪ reference-scan ∪ untextured ∪ hidden).
- K-wise is ADDITIVE — pairwise remains; when no quad is available, fall back to `_build_comparison`.
- Gold/attention checks stay pairwise-only in v1.
- Rate limit charged ONCE per ballot, not per derived vote.
- K fixed at 4.
- Test runner `.venv/bin/pytest`. NEVER `BIO3D_DATABASE_URL=study`.

## File Structure

- `app/models.py` — add `KBallot`; add `ballot_id` to `Comparison`.
- `app/seed.py` — add `KBallot` to `_FORCE_DELETE_MODELS` (import it).
- `app/matchmaking.py` — add `pick_quad`.
- `app/ranking.py` — add `groups` param to `bradley_terry` + `_bootstrap_scores` (ballot-level resampling).
- `app/service.py` — `_matches_for_scope` returns `(matches, groups)`; add `resolve_kballot`; update the 2 callers.
- `app/schemas.py` — add `KVoteIn`.
- `app/integrity.py` — add `seen_quads_for`.
- `app/main.py` — add `_build_kwise_comparison`, `mode=kwise` branch in `api_next`, `POST /api/kvote`.
- `app/templates/arena.html` + `app/static/arena.js` — render 4-up vs 2-up by payload shape.
- Tests: `tests/test_kwise_model.py`, `tests/test_pick_quad.py`, `tests/test_ballot_bootstrap.py`, `tests/test_kvote_endpoint.py`.

---

### Task 1: Schema — `KBallot` table + `Comparison.ballot_id` + drift guard

**Files:**

- Modify: `app/models.py` (add `KBallot`; add `ballot_id` to `Comparison`)
- Modify: `app/seed.py:54` (`_FORCE_DELETE_MODELS`)
- Test: `tests/test_kwise_model.py`

**Interfaces:**

- Produces: `KBallot(id, task_id, criterion_id, session_id, output_ids_json, best_output_id, resolved, created)`; `Comparison.ballot_id: int | None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_kwise_model.py
import json
from app import seed
from app.database import SessionLocal, init_db
from app.models import KBallot, Comparison, ModelOutput


def setup_module(_m):
    init_db()


def test_kballot_in_force_delete_models():
    assert KBallot in seed._FORCE_DELETE_MODELS


def test_kballot_and_ballot_id_persist():
    with SessionLocal() as db:
        b = KBallot(task_id=1, criterion_id=1, session_id="s", output_ids_json=json.dumps([1, 2, 3, 4]))
        db.add(b)
        db.flush()
        assert b.resolved is False
        c = Comparison(task_id=1, output_a_id=1, output_b_id=2, criterion_id=1, session_id="s", ballot_id=b.id)
        db.add(c)
        db.flush()
        assert c.ballot_id == b.id
        db.rollback()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_kwise_model.py -v`
Expected: FAIL (`ImportError: cannot import name 'KBallot'`).

- [ ] **Step 3: Add the model + column**

In `app/models.py`, add `ballot_id` to `Comparison` (after `gold_expected`, before `created`):

```python
    ballot_id: Mapped[int | None] = mapped_column(ForeignKey("kballot.id"), nullable=True, index=True)
```

And add the new model (place it directly after the `Vote` class):

```python
class KBallot(Base):
    """A simultaneous K-up ballot: K=4 outputs shown, voter picks the single best.
    Resolves into K-1 pairwise Comparison+Vote rows (best beats each other), all sharing
    ballot_id=this.id. best_output_id NULL after resolution = 'can't tell / all bad' (0 relations)."""

    __tablename__ = "kballot"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("task.id"), index=True)
    criterion_id: Mapped[int] = mapped_column(ForeignKey("criterion.id"))
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    output_ids_json: Mapped[str] = mapped_column(Text, default="[]")  # the 4 output ids shown
    best_output_id: Mapped[int | None] = mapped_column(ForeignKey("model_output.id"), nullable=True)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    created: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)
```

In `app/seed.py`: import `KBallot` alongside the other model imports, and insert it into `_FORCE_DELETE_MODELS` immediately AFTER `Comparison` (Comparison FK-references KBallot, so Comparison must be deleted first; KBallot FK-references ModelOutput, so it precedes ModelOutput):

```python
_FORCE_DELETE_MODELS = (
    Vote,
    Comparison,
    KBallot,
    GoldPair,
    # ... rest unchanged ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_kwise_model.py tests/test_seed_force_cascade.py -v`
Expected: PASS (drift guard stays green — `KBallot` is a `ModelOutput` child via `best_output_id` and is now listed).

- [ ] **Step 5: Commit**

```bash
git add app/models.py app/seed.py tests/test_kwise_model.py
git commit -m "feat(kwise): KBallot table + Comparison.ballot_id + drift guard"
```

---

### Task 2: Matchmaking — `pick_quad`

**Files:**

- Modify: `app/matchmaking.py` (add `pick_quad`)
- Test: `tests/test_pick_quad.py`

**Interfaces:**

- Consumes: `_real_outputs`, `_paradigm_groups` (existing).
- Produces: `pick_quad(db, task, exclude_fn=None, seen_quads=None) -> list[ModelOutput] | None` — 4 distinct same-paradigm outputs (least-compared, shuffled), or `None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pick_quad.py
import uuid
from app import matchmaking
from app.database import SessionLocal, init_db
from app.models import Generator, ModelOutput, Task


def setup_module(_m):
    init_db()


def _task_with(db, paradigm_counts):
    t = Task(title=f"q-{uuid.uuid4().hex[:8]}", prompt="p", category_id=1)
    db.add(t)
    db.flush()
    for para, n in paradigm_counts.items():
        for _ in range(n):
            g = Generator(slug=f"g-{uuid.uuid4().hex}", name="g", kind="model", paradigm=para)
            db.add(g)
            db.flush()
            db.add(ModelOutput(task_id=t.id, generator_id=g.id, asset_path="x.glb", asset_format="glb"))
    db.flush()
    return t


def test_quad_returned_when_four_same_paradigm():
    with SessionLocal() as db:
        t = _task_with(db, {"procedural": 4})
        quad = matchmaking.pick_quad(db, t)
        assert quad is not None and len(quad) == 4
        assert len({o.id for o in quad}) == 4
        assert len({o.generator.paradigm for o in quad}) == 1
        db.rollback()


def test_none_when_fewer_than_four_in_any_group():
    with SessionLocal() as db:
        t = _task_with(db, {"procedural": 3, "text_native": 3})  # no single group has 4
        assert matchmaking.pick_quad(db, t) is None
        db.rollback()


def test_exclude_fn_applied_before_counting():
    with SessionLocal() as db:
        t = _task_with(db, {"procedural": 5})
        outs = sorted(matchmaking._real_outputs(t), key=lambda o: o.id)
        drop = {outs[0].id, outs[1].id}
        quad = matchmaking.pick_quad(db, t, exclude_fn=lambda o: o.id in drop)
        assert quad is not None and len(quad) == 4  # 5 - 2 excluded = 3? -> expect None
        db.rollback()
```

Note: fix the last test's expectation in Step 3 once behavior is implemented (5−2=3 → `None`); written here to be corrected to `assert quad is None`.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_pick_quad.py -v`
Expected: FAIL (`AttributeError: module 'app.matchmaking' has no attribute 'pick_quad'`).

- [ ] **Step 3: Implement `pick_quad`; fix the exclude test**

In `tests/test_pick_quad.py`, change the last test body to expect `None` (5 − 2 excluded = 3 < 4):

```python
def test_exclude_fn_applied_before_counting():
    with SessionLocal() as db:
        t = _task_with(db, {"procedural": 5})
        outs = sorted(matchmaking._real_outputs(t), key=lambda o: o.id)
        drop = {outs[0].id, outs[1].id}
        assert matchmaking.pick_quad(db, t, exclude_fn=lambda o: o.id in drop) is None
        db.rollback()
```

Add to `app/matchmaking.py` (after `pick_pair`):

```python
def pick_quad(
    db: Session, task: Task, exclude_fn=None, seen_quads=None
) -> list[ModelOutput] | None:
    """Pick 4 distinct same-paradigm (non-gold) outputs for the task, biased toward least-compared.

    Mirrors pick_pair: exclude_fn filters the pool first (admissibility etc.), outputs are grouped
    by paradigm, the least-sampled group with >=4 members is chosen, and its 4 least-sampled
    outputs are returned in shuffled order. `seen_quads` (set of frozenset of 4 output ids) is the
    session's already-served quads; a quad already seen is skipped. Returns None when no group has
    4 fresh outputs (caller falls back to pairwise)."""
    outputs = _real_outputs(task)
    if exclude_fn is not None:
        outputs = [o for o in outputs if not exclude_fn(o)]
    groups = _paradigm_groups(outputs)
    seen = seen_quads or set()
    remaining = [g for g in groups.values() if len(g) >= 4]
    while remaining:
        best = min(min(o.n_comparisons for o in g) for g in remaining)
        tied = [g for g in remaining if min(o.n_comparisons for o in g) == best]
        chosen = random.choice(tied)
        ordered = list(chosen)
        random.shuffle(ordered)
        ordered.sort(key=lambda o: o.n_comparisons)
        quad = ordered[:4]
        if frozenset(o.id for o in quad) not in seen:
            random.shuffle(quad)
            return quad
        remaining = [g for g in remaining if g is not chosen]
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_pick_quad.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add app/matchmaking.py tests/test_pick_quad.py
git commit -m "feat(kwise): pick_quad — 4 least-compared same-paradigm outputs"
```

---

### Task 3: Ranking — ballot-level bootstrap

**Files:**

- Modify: `app/ranking.py` (`_bootstrap_scores`, `bradley_terry` gain `groups`)
- Modify: `app/service.py` (`_matches_for_scope` returns `(matches, groups)`; update callers at ~243 and ~271)
- Test: `tests/test_ballot_bootstrap.py`

**Interfaces:**

- Produces: `bradley_terry(players, matches, bootstrap=200, reg=0.1, seed=12345, groups=None)`; `_matches_for_scope(...) -> tuple[list[tuple[int,int]], list[int]]`.
- Consumes: nothing new.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ballot_bootstrap.py
from app import ranking


def _width(res, pid):
    return res.upper[pid] - res.lower[pid]


def test_ballot_grouping_widens_cis():
    # One ballot's 3 correlated matches (all A>others) vs treating them independently.
    players = [1, 2, 3, 4]
    matches = [(1, 2), (1, 3), (1, 4)] * 20  # 60 matches, 20 identical ballots
    groups = [g for g in range(20) for _ in range(3)]  # 3 matches share each ballot id
    naive = ranking.bradley_terry(players, matches, bootstrap=200)
    grouped = ranking.bradley_terry(players, matches, bootstrap=200, groups=groups)
    # Point estimates identical; grouped CIs must be >= naive CIs (resampling 20 ballots,
    # not 60 pseudo-independent pairs, cannot be MORE certain).
    assert grouped.scores[1] == naive.scores[1]
    assert _width(grouped, 1) >= _width(naive, 1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_ballot_bootstrap.py -v`
Expected: FAIL (`TypeError: bradley_terry() got an unexpected keyword argument 'groups'`).

- [ ] **Step 3: Implement grouping**

Replace `_bootstrap_scores` in `app/ranking.py` with:

```python
def _bootstrap_scores(
    players: list[int],
    matches: list[tuple[int, int]],
    bootstrap: int,
    reg: float = 0.1,
    seed: int = 12345,
    groups: list[int] | None = None,
) -> dict[int, list[float]]:
    """Resample the match list `bootstrap` times; return per-player Elo-score samples.

    If `groups` is given (same length as matches), resample whole BALLOT GROUPS with
    replacement instead of individual pairs — the K-1 pairs derived from one K-ballot are
    NOT independent, so per-pair resampling fake-tightens the CIs. Native pairwise votes each
    form their own singleton group. Index b across players is the SAME resample (paired)."""
    samples: dict[int, list[float]] = defaultdict(list)
    if bootstrap <= 0 or not matches:
        return samples
    rng = random.Random(seed)
    if groups is not None:
        by_group: dict[int, list[tuple[int, int]]] = defaultdict(list)
        for m, g in zip(matches, groups):
            by_group[g].append(m)
        keys = list(by_group.keys())
        gk = len(keys)
        for _ in range(bootstrap):
            resampled: list[tuple[int, int]] = []
            for _ in range(gk):
                resampled.extend(by_group[keys[rng.randrange(gk)]])
            elo = _strength_to_elo(_fit_strengths(players, resampled, reg=reg))
            for pid, val in elo.items():
                samples[pid].append(val)
        return samples
    n = len(matches)
    for _ in range(bootstrap):
        resampled = [matches[rng.randrange(n)] for _ in range(n)]
        elo = _strength_to_elo(_fit_strengths(players, resampled, reg=reg))
        for pid, val in elo.items():
            samples[pid].append(val)
    return samples
```

Update `bradley_terry` signature + the `_bootstrap_scores` call:

```python
def bradley_terry(
    players: list[int],
    matches: list[tuple[int, int]],
    bootstrap: int = 200,
    reg: float = 0.1,
    seed: int = 12345,
    groups: list[int] | None = None,
) -> BTResult:
```

and change the bootstrap line inside it to:

```python
    samples = _bootstrap_scores(players, matches, bootstrap, reg=reg, seed=seed, groups=groups)
```

In `app/service.py`, change `_matches_for_scope` to also build a parallel `groups` list and return both. Add `groups: list[int] = []` next to `matches`, and on EACH `matches.append(...)` append the group key (for a tie, append it twice — once per direction):

```python
    matches: list[tuple[int, int]] = []
    groups: list[int] = []
    for vote, comparison in db.execute(stmt).all():
        # ... existing skips (bad / dangling / ref / cross-paradigm) unchanged ...
        gkey = comparison.ballot_id if comparison.ballot_id is not None else -comparison.id
        if vote.winner == "a":
            matches.append((gen_a, gen_b)); groups.append(gkey)
        elif vote.winner == "b":
            matches.append((gen_b, gen_a)); groups.append(gkey)
        elif vote.winner == "tie" and include_ties:
            matches.append((gen_a, gen_b)); groups.append(gkey)
            matches.append((gen_b, gen_a)); groups.append(gkey)
    return matches, groups
```

Update the return type annotation to `tuple[list[tuple[int, int]], list[int]]`, and update the two callers:

At `recompute_scope` (~271):

```python
    matches, groups = _matches_for_scope(db, criterion.id, category_id)
    players = sorted(set(_players_for_scope(db, category_id)) | {p for m in matches for p in m})
    result = ranking.bradley_terry(players, matches, bootstrap=config.BT_BOOTSTRAP, groups=groups)
```

At the verified-board build (~243):

```python
    matches, groups = _matches_for_scope(db, crit.id, category_id, verified_only=True)
    result = ranking.bradley_terry(players, matches, bootstrap=config.BT_BOOTSTRAP, groups=groups)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_ballot_bootstrap.py tests/test_ranking.py -v`
Expected: PASS (grouping test green; existing ranking tests unaffected — default `groups=None` preserves behavior).

- [ ] **Step 5: Commit**

```bash
git add app/ranking.py app/service.py tests/test_ballot_bootstrap.py
git commit -m "feat(kwise): ballot-level bootstrap so derived pairs don't fake-tighten CIs"
```

---

### Task 4: Endpoints + decomposition — `KVoteIn`, `_build_kwise_comparison`, `/api/kvote`

**Files:**

- Modify: `app/schemas.py` (add `KVoteIn`)
- Modify: `app/integrity.py` (add `seen_quads_for`)
- Modify: `app/service.py` (add `resolve_kballot`)
- Modify: `app/main.py` (add `_build_kwise_comparison`, `mode=kwise` in `api_next`, `POST /api/kvote`)
- Test: `tests/test_kvote_endpoint.py`

**Interfaces:**

- Consumes: `matchmaking.pick_quad`, `service.apply_vote`, `KBallot`, `Comparison`, `Vote`.
- Produces: `KVoteIn(ballot_id: int, best_output_id: int | None)`; `service.resolve_kballot(db, ballot, best_output_id, session_id) -> int` (returns # relations created); `integrity.seen_quads_for(db, session_id, criterion_id) -> set[frozenset[int]]`.

- [ ] **Step 1: Write the failing test** (uses FastAPI TestClient like the existing endpoint tests)

```python
# tests/test_kvote_endpoint.py
import json
from app.database import SessionLocal, init_db
from app.models import KBallot, Comparison, Vote


def setup_module(_m):
    init_db()


def test_resolve_kballot_decomposes_to_three_pairs():
    from app import service
    with SessionLocal() as db:
        b = KBallot(task_id=1, criterion_id=1, session_id="s", output_ids_json=json.dumps([10, 11, 12, 13]))
        db.add(b)
        db.flush()
        n = service.resolve_kballot(db, b, best_output_id=10, session_id="s")
        assert n == 3
        assert b.resolved is True
        comps = db.query(Comparison).filter_by(ballot_id=b.id).all()
        assert len(comps) == 3
        # every derived comparison has best (10) in slot a and winner 'a'
        for c in comps:
            assert c.output_a_id == 10 and c.output_b_id in (11, 12, 13)
            assert c.vote.winner == "a"
        db.rollback()


def test_resolve_all_bad_makes_no_relations():
    from app import service
    with SessionLocal() as db:
        b = KBallot(task_id=1, criterion_id=1, session_id="s", output_ids_json=json.dumps([20, 21, 22, 23]))
        db.add(b)
        db.flush()
        n = service.resolve_kballot(db, b, best_output_id=None, session_id="s")
        assert n == 0
        assert b.resolved is True
        assert db.query(Comparison).filter_by(ballot_id=b.id).count() == 0
        db.rollback()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_kvote_endpoint.py -v`
Expected: FAIL (`AttributeError: module 'app.service' has no attribute 'resolve_kballot'`).

- [ ] **Step 3: Implement schema, decomposition, helper, endpoints**

`app/schemas.py` — add:

```python
class KVoteIn(BaseModel):
    ballot_id: int
    best_output_id: int | None = None
```

`app/service.py` — add (near `apply_vote`; imports `json`, `KBallot`, `Comparison`, `Vote` already available or add them):

```python
def resolve_kballot(db: Session, ballot, best_output_id: int | None, session_id: str) -> int:
    """Resolve a K-ballot. best_output_id=None → 'all bad' (0 relations). Otherwise expand into
    one (best beats loser) Comparison+Vote per loser, sharing ballot_id, each fed to apply_vote.
    Sets ballot.resolved. Returns the number of pairwise relations created. Caller commits."""
    import json as _json

    ballot.best_output_id = best_output_id
    ballot.resolved = True
    if best_output_id is None:
        return 0
    ids = _json.loads(ballot.output_ids_json)
    losers = [oid for oid in ids if oid != best_output_id]
    for loser in losers:
        comp = Comparison(
            task_id=ballot.task_id,
            output_a_id=best_output_id,
            output_b_id=loser,
            criterion_id=ballot.criterion_id,
            session_id=session_id,
            ballot_id=ballot.id,
        )
        db.add(comp)
        db.flush()
        vote = Vote(comparison_id=comp.id, winner="a", session_id=session_id)
        db.add(vote)
        db.flush()
        apply_vote(db, vote)
    return len(losers)
```

`app/integrity.py` — add:

```python
def seen_quads_for(db, session_id: str, criterion_id: int) -> set[frozenset[int]]:
    """Frozensets of the 4 output ids for every KBallot this session already saw for the criterion."""
    import json as _json

    from .models import KBallot

    rows = (
        db.query(KBallot.output_ids_json)
        .filter(KBallot.session_id == session_id, KBallot.criterion_id == criterion_id)
        .all()
    )
    return {frozenset(_json.loads(r[0])) for r in rows}
```

`app/main.py` — add `_build_kwise_comparison` (mirrors `_build_comparison`'s exclusion but uses `pick_quad`, NO gold, and falls back to pairwise):

```python
def _build_kwise_comparison(
    db: Session,
    session_id: str,
    criterion_slug: str | None = None,
    category_slug: str | None = None,
) -> dict | None:
    """Serve a 4-up K-ballot (no gold in kwise). Falls back to a pairwise comparison when no task
    has >=4 admitted same-paradigm fresh outputs."""
    import json as _json
    import random as _random

    from .sourcing import is_reference_scan, is_untextured_output
    from . import admissibility

    crit = None
    if criterion_slug:
        crit = db.execute(select(Criterion).where(Criterion.slug == criterion_slug)).scalars().first()
    if crit is None:
        crit = _default_criterion(db)

    category_id = _resolve_category_id(db, category_slug)
    _gated = admissibility.non_admitted_output_ids(db)

    def _vote_excluded(o):
        return (
            is_reference_scan(o.source)
            or is_untextured_output(o)
            or o.hidden_at is not None
            or o.id in _gated
        )

    seen = integrity.seen_quads_for(db, session_id, crit.id)
    stmt = select(Task).where(Task.active.is_(True))
    if category_id is not None:
        stmt = stmt.where(Task.category_id == category_id)
    tasks = list(db.execute(stmt).scalars().all())
    _random.shuffle(tasks)
    for task in tasks:
        quad = matchmaking.pick_quad(db, task, exclude_fn=_vote_excluded, seen_quads=seen)
        if quad is None:
            continue
        ballot = KBallot(
            task_id=task.id,
            criterion_id=crit.id,
            session_id=session_id,
            output_ids_json=_json.dumps([o.id for o in quad]),
        )
        db.add(ballot)
        db.commit()
        return {
            "kind": "kwise",
            "ballot_id": ballot.id,
            "task": {"id": task.id, "title": task.title, "prompt": task.prompt},
            "criterion": {"slug": crit.slug, "name": crit.name},
            "outputs": [_serialize_output(o) for o in quad],
        }
    # No quad anywhere → transparent pairwise fallback.
    return _build_comparison(db, session_id, criterion_slug, category_slug)
```

Note for implementer: `_serialize_output` — reuse whatever per-output serializer `_serialize` uses for a single output (grep `_serialize` in `app/main.py`; if there is no standalone helper, inline `{"output_id": o.id, "asset_url": ..., "format": o.asset_format}` matching the anonymized fields `_serialize` exposes for `output_a`). Do NOT leak `generator` identity.

`api_next` — add the branch (before the existing `else`):

```python
    if mode == "calibration":
        payload = _build_calibration_comparison(db, request.state.session_id)
    elif mode == "kwise":
        payload = _build_kwise_comparison(db, request.state.session_id, criterion, category)
    else:
        payload = _build_comparison(db, request.state.session_id, criterion, category)
```

Add the endpoint (after `api_vote`), importing `KVoteIn` and `KBallot`:

```python
@app.post("/api/kvote")
def api_kvote(
    kvote_in: KVoteIn,
    request: Request,
    db: Session = Depends(get_db),
    criterion: str | None = None,
    category: str | None = None,
    x_captcha_token: str | None = Header(default=None),
):
    import json as _json

    sid = request.state.session_id
    if not integrity.verify_captcha(x_captcha_token):
        raise HTTPException(403, "Captcha verification required/failed")
    if not integrity.check_rate_limit(sid):
        raise HTTPException(429, "Rate limit exceeded — slow down")
    ballot = db.get(KBallot, kvote_in.ballot_id)
    if ballot is None:
        raise HTTPException(404, "Unknown ballot")
    if ballot.resolved:
        raise HTTPException(409, "Ballot already resolved")
    ids = _json.loads(ballot.output_ids_json)
    if kvote_in.best_output_id is not None and kvote_in.best_output_id not in ids:
        raise HTTPException(400, "best_output_id not among the shown outputs")
    service.resolve_kballot(db, ballot, kvote_in.best_output_id, sid)
    integrity.note_vote(db, sid)  # ONE rate-accounting per ballot, not per derived vote
    db.commit()
    nxt = _build_kwise_comparison(db, sid, criterion, category)
    return {"status": "ok", "next": nxt}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_kvote_endpoint.py -v`
Expected: PASS (both decomposition tests).

- [ ] **Step 5: Commit**

```bash
git add app/schemas.py app/integrity.py app/service.py app/main.py tests/test_kvote_endpoint.py
git commit -m "feat(kwise): KVoteIn + resolve_kballot + /api/kvote + mode=kwise next"
```

---

### Task 5: Client — 4-up arena UI

**Files:**

- Modify: `app/templates/arena.html`
- Modify: `app/static/arena.js`
- Test: `tests/test_kwise_page.py` (server-side scaffold check; live UX verified via Playwright at launch)

**Interfaces:**

- Consumes: `/api/next?set=kwise` (returns `{kind:"kwise", ballot_id, outputs:[4], ...}` or a pairwise payload), `POST /api/kvote`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_kwise_page.py
from fastapi.testclient import TestClient
from app.main import app


def test_arena_page_has_kwise_scaffold():
    c = TestClient(app)
    r = c.get("/arena")
    assert r.status_code == 200
    assert "kwise-grid" in r.text  # the 4-up container the JS toggles
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_kwise_page.py -v`
Expected: FAIL (`assert 'kwise-grid' in r.text`).

- [ ] **Step 3: Add the 4-up scaffold + JS branch**

In `app/templates/arena.html`, add a hidden 4-up container beside the existing 2-up (match the existing viewer markup/classes; a 2×2 grid of `<model-viewer>` slots with a "Pick best" click per slot and a "Can't tell / all bad" button):

```html
<div id="kwise-grid" class="kwise-grid" hidden>
  <!-- 4 slots injected by arena.js; each slot is a clickable model-viewer -->
</div>
<button id="kwise-allbad" hidden>Can't tell / all bad</button>
```

In `app/static/arena.js`, branch the render on payload shape (reuse the existing viewer-init + POST helpers):

```javascript
function render(payload) {
  if (payload && payload.kind === "kwise") {
    renderKwise(payload); // show #kwise-grid (4 clickable viewers), hide 2-up
  } else {
    renderPair(payload); // existing 2-up path
  }
}

function renderKwise(p) {
  document.getElementById("kwise-grid").hidden = false;
  document.getElementById("kwise-allbad").hidden = false;
  // hide the existing pair container here
  const grid = document.getElementById("kwise-grid");
  grid.innerHTML = "";
  p.outputs.forEach((o) => {
    const slot = makeViewerSlot(o); // reuse existing per-output viewer builder
    slot.onclick = () => submitKvote(p.ballot_id, o.output_id);
    grid.appendChild(slot);
  });
  document.getElementById("kwise-allbad").onclick = () =>
    submitKvote(p.ballot_id, null);
}

async function submitKvote(ballotId, bestOutputId) {
  const res = await fetch("/api/kvote", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ballot_id: ballotId, best_output_id: bestOutputId }),
  });
  if (!res.ok) {
    showError(await res.text());
    return;
  } // honor res.ok (arena.js vote() lesson)
  const data = await res.json();
  render(data.next);
}
```

Point the arena's "next" fetch at `set=kwise` when K-wise mode is active (a mode toggle or default): fetch `/api/next?set=kwise`. Since `_build_kwise_comparison` falls back to a pairwise payload, `render()` already handles both shapes, so requesting kwise is always safe.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_kwise_page.py -v`
Expected: PASS.

- [ ] **Step 5: Commit + note live verification**

```bash
git add app/templates/arena.html app/static/arena.js tests/test_kwise_page.py
git commit -m "feat(kwise): 4-up arena UI (pick-best / all-bad), payload-shape branching"
```

The JS render path (4-up grid, click-to-vote, fallback to 2-up) is verified live via Playwright at launch — no JS unit harness exists in this repo; the server scaffold test + the endpoint tests (Task 4) cover the Python side.

---

## Self-Review

**Spec coverage:** data model (T1) ✓; matchmaking within-paradigm + admitted + ≥4 + dedup (T2 + T4's `_vote_excluded`/`seen_quads`) ✓; ranking reuse + ballot bootstrap (T3) ✓; decomposition + endpoints + fallback + rate-once + all-bad (T4) ✓; gold pairwise-only (T4 omits gold from `_build_kwise_comparison`) ✓; client 4-up (T5) ✓. Out-of-scope items (UCB, best-worst, Rank mode, K-wise gold) are not implemented — correct.

**Placeholder scan:** `_serialize_output` is flagged with an explicit resolution instruction (reuse `_serialize`'s per-output fields) rather than left as TBD; arena.html/js reuse existing helpers named explicitly (`makeViewerSlot`, `renderPair`). Implementer must grep `_serialize` in `app/main.py` to match the exact anonymized output fields — called out in T4 Step 3.

**Type consistency:** `pick_quad -> list[ModelOutput] | None`; `resolve_kballot(...) -> int`; `bradley_terry(..., groups=None)`; `_matches_for_scope -> (matches, groups)` with all callers updated; `KVoteIn(ballot_id, best_output_id)` matches the `/api/kvote` body and `resolve_kballot` args; `KBallot` fields match across T1/T4. Consistent.
