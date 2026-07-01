# Multi-Paradigm Arena — Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `paradigm` a first-class dimension on generators and guarantee outputs are only ever compared and ranked within their paradigm, with existing generators backfilled and a minimal UI to see it.

**Architecture:** A pure `app/paradigms.py` holds the vocabulary + predicates; `Generator` gains a `paradigm` column; a backfill script classifies existing generators (fail-loud); matchmaking pairs only within a paradigm and rating aggregation counts only within-paradigm comparisons; the leaderboard/coverage views gain a paradigm column + filter. Reuses all existing BT/matchmaking machinery — the guardrail is a same-paradigm predicate inserted at pair-birth and score-tally.

**Tech Stack:** Python 3.13 (`.venv`), FastAPI + Jinja2, SQLAlchemy (`create_all`, no migrations), pytest.

## Global Constraints

- `paradigm` lives on `Generator` (String(32), default "", indexed); outputs inherit via their generator. Additive `create_all`, no migration tooling.
- Vocabulary (single source of truth in `app/paradigms.py`): used-at-backfill `image_recon, capture_scan, procedural_llm, procedural_expert, retrieval`; reserved `text_native, video, texturing, agentic, sketch`.
- Guardrail = "never compare across different paradigm values." Same paradigm _value_ (including two empties pre-backfill) may pair — so the change is backward-compatible until backfill runs, and strict after.
- Backfill **fails loud** on any unmapped generator (never default-assign).
- Tests run under the DEFAULT env only (`.venv/bin/python -m pytest`). NEVER set `BIO3D_DATABASE_URL`/`BIO3D_DATA_DIR` (study-DB wipe incident).
- Out of scope (later per-paradigm sub-projects): pass@k, morphology fidelity, texturing, new generators, per-paradigm pages.

---

### Task 1: Paradigm vocabulary + predicates

**Files:**

- Create: `app/paradigms.py`
- Test: `tests/test_paradigms.py`

**Interfaces:**

- Produces: `PARADIGMS: tuple[str, ...]`, `BACKFILL_PARADIGMS: tuple[str, ...]`, `DISPLAY_NAMES: dict[str, str]`, `is_valid_paradigm(p: str) -> bool`, `same_paradigm(a: str, b: str) -> bool`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_paradigms.py
from __future__ import annotations

from app import paradigms


def test_vocabulary_and_validity():
    for p in ("image_recon", "capture_scan", "procedural_llm", "procedural_expert", "retrieval"):
        assert p in paradigms.PARADIGMS and p in paradigms.BACKFILL_PARADIGMS
    for p in ("text_native", "video", "texturing", "agentic", "sketch"):
        assert p in paradigms.PARADIGMS and p not in paradigms.BACKFILL_PARADIGMS
    assert paradigms.is_valid_paradigm("image_recon") is True
    assert paradigms.is_valid_paradigm("nope") is False


def test_display_names_cover_all():
    for p in paradigms.PARADIGMS:
        assert p in paradigms.DISPLAY_NAMES and paradigms.DISPLAY_NAMES[p]


def test_same_paradigm():
    assert paradigms.same_paradigm("image_recon", "image_recon") is True
    assert paradigms.same_paradigm("image_recon", "retrieval") is False
    # two empties (pre-backfill) count as same group; empty vs tagged does not
    assert paradigms.same_paradigm("", "") is True
    assert paradigms.same_paradigm("", "image_recon") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_paradigms.py -q`
Expected: FAIL — `app.paradigms` does not exist.

- [ ] **Step 3: Write minimal implementation**

```python
# app/paradigms.py
"""The paradigm dimension: which 3D-creation approach a generator uses. Pure module (no DB)
so the vocabulary + predicates are unit-tested and shared by the model, backfill, matchmaking,
rating aggregation, and UI. Ranking is ALWAYS within a single paradigm value."""

from __future__ import annotations

# Present in the data today (backfill assigns these):
BACKFILL_PARADIGMS: tuple[str, ...] = (
    "image_recon",
    "capture_scan",
    "procedural_llm",
    "procedural_expert",
    "retrieval",
)
# Reserved so the enum is stable as future tracks land:
_RESERVED: tuple[str, ...] = ("text_native", "video", "texturing", "agentic", "sketch")
PARADIGMS: tuple[str, ...] = BACKFILL_PARADIGMS + _RESERVED

DISPLAY_NAMES: dict[str, str] = {
    "image_recon": "Image→3D reconstruction",
    "capture_scan": "Scan / capture",
    "procedural_llm": "LLM procedural (code-gen)",
    "procedural_expert": "Expert / simulation procedural",
    "retrieval": "Retrieved asset",
    "text_native": "Text→3D (native)",
    "video": "Video→3D / 4D",
    "texturing": "Texturing / editing",
    "agentic": "Agentic 3D",
    "sketch": "Sketch→3D",
}


def is_valid_paradigm(p: str) -> bool:
    return p in PARADIGMS


def same_paradigm(a: str, b: str) -> bool:
    """True iff two paradigm values are equal. Empty==empty is True (all-untagged generators
    form one group pre-backfill, keeping matchmaking backward-compatible); empty vs a tagged
    value is False so a half-backfilled state never silently crosses paradigms."""
    return a == b
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_paradigms.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add app/paradigms.py tests/test_paradigms.py
git commit -m "feat(paradigm): vocabulary + same_paradigm predicate"
```

---

### Task 2: `Generator.paradigm` column

**Files:**

- Modify: `app/models.py` (the `Generator` class, currently ends at the `outputs` relationship)
- Test: `tests/test_paradigm_model.py`

**Interfaces:**

- Consumes: nothing.
- Produces: `Generator.paradigm` (str, default "", indexed) readable/writable and created by `init_db`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_paradigm_model.py
from __future__ import annotations

from app.database import SessionLocal, init_db
from app.models import Generator


def setup_module(_m):
    init_db()


def test_generator_paradigm_defaults_empty_and_persists():
    with SessionLocal() as db:
        g = Generator(slug="pgm-test-gen", name="t", kind="model")
        db.add(g)
        db.commit()
        assert g.paradigm == ""  # default
        g.paradigm = "image_recon"
        db.commit()
        got = db.query(Generator).filter_by(slug="pgm-test-gen").one()
        assert got.paradigm == "image_recon"
        db.delete(got)
        db.commit()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_paradigm_model.py -q`
Expected: FAIL — `AttributeError`/`TypeError` on `paradigm` (column doesn't exist).

- [ ] **Step 3: Write minimal implementation**

In `app/models.py`, add one line to the `Generator` class, right after `is_anonymous`:

```python
    is_anonymous: Mapped[bool] = mapped_column(Boolean, default=True)
    paradigm: Mapped[str] = mapped_column(String(32), default="", index=True)  # see app/paradigms.py
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_paradigm_model.py -q`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add app/models.py tests/test_paradigm_model.py
git commit -m "feat(paradigm): Generator.paradigm column"
```

---

### Task 3: Backfill classifier + script

**Files:**

- Create: `scripts/backfill_paradigms.py`
- Test: `tests/test_backfill_paradigms.py`

**Interfaces:**

- Consumes: `Generator` (Task 2), `paradigms.BACKFILL_PARADIGMS`.
- Produces: `classify(slug: str, kind: str, sources: set[str]) -> str | None` (returns a paradigm or None if unmatched); `assign_paradigms(db, *, commit: bool) -> dict` (returns `{"assigned": {slug: paradigm}, "unmapped": [slug,...]}`; raises `ValueError` listing unmapped slugs when `commit=True` and any are unmapped).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_backfill_paradigms.py
from __future__ import annotations

import pytest

from scripts.backfill_paradigms import classify


def test_classify_each_family():
    assert classify("openrouter-anthropic-claude-opus-4-8", "model", set()) == "procedural_llm"
    assert classify("lpy-maize", "model", {"bio3d-arena"}) == "procedural_expert"
    assert classify("infinigen-rose", "model", set()) == "procedural_expert"
    assert classify("hunyuan3d", "model", {"api:hunyuan"}) == "image_recon"
    assert classify("g1", "model", {"api:tripo"}) == "image_recon"
    assert classify("icrisat-sorghum", "model", {"icrisat"}) == "capture_scan"
    assert classify("g2", "model", {"sketchfab"}) == "retrieval"
    assert classify("g3", "model", {"objaverse"}) == "retrieval"


def test_classify_unknown_returns_none():
    assert classify("totally-unknown-gen", "model", {"mystery"}) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_backfill_paradigms.py -q`
Expected: FAIL — module/`classify` not defined.

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/backfill_paradigms.py
"""Classify every existing Generator into a paradigm and set Generator.paradigm.

Dry-run by default (prints the generator->paradigm table + any unmapped). `--commit` writes,
and REFUSES to write if any generator is unmapped (fail loud — never default-assign). Run
against the target DB via BIO3D_DATABASE_URL; classification uses the generator slug + the
set of `source` strings across its outputs."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import Generator, ModelOutput  # noqa: E402


def classify(slug: str, kind: str, sources: set[str]) -> str | None:
    """Return a paradigm for a generator, or None if no rule matches. Order matters:
    most-specific families first."""
    s = slug.lower()
    src = {x.lower() for x in sources}

    def any_in(needles, hay):
        return any(n in h for n in needles for h in hay)

    if s.startswith("openrouter-"):
        return "procedural_llm"
    if any(k in s for k in ("lpy", "l-py", "lsystem", "infinigen", "procedural")):
        return "procedural_expert"
    if "sketchfab" in s or "objaverse" in s or any_in(("sketchfab", "objaverse"), src):
        return "retrieval"
    if any(k in s for k in ("icrisat", "romi", "scan")) or any_in(
        ("icrisat", "romi", "scan", "reference"), src
    ):
        return "capture_scan"
    if any(k in s for k in ("hunyuan", "tripo", "partcrafter", "meshy", "trellis", "recon")) or any(
        h.startswith("api:") for h in src
    ):
        return "image_recon"
    return None


def assign_paradigms(db, *, commit: bool) -> dict:
    gens = db.execute(select(Generator)).scalars().all()
    assigned: dict[str, str] = {}
    unmapped: list[str] = []
    for g in gens:
        sources = {
            o.source
            for o in db.execute(
                select(ModelOutput).where(ModelOutput.generator_id == g.id)
            ).scalars()
        }
        p = classify(g.slug, g.kind, sources)
        if p is None:
            unmapped.append(g.slug)
        else:
            assigned[g.slug] = p
    if commit:
        if unmapped:
            raise ValueError(f"unmapped generators (refusing to write): {sorted(unmapped)}")
        for g in gens:
            g.paradigm = assigned[g.slug]
        db.commit()
    return {"assigned": assigned, "unmapped": unmapped}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--commit", action="store_true", help="write paradigms (else dry-run)")
    args = ap.parse_args(argv)
    with SessionLocal() as db:
        res = assign_paradigms(db, commit=args.commit)
    for slug, p in sorted(res["assigned"].items()):
        print(f"  {slug:40s} -> {p}")
    if res["unmapped"]:
        print(f"\nUNMAPPED ({len(res['unmapped'])}): {sorted(res['unmapped'])}")
        print("Add rules for these before --commit.")
        return 0 if not args.commit else 1
    print(f"\n{len(res['assigned'])} generators classified; unmapped: 0"
          + ("" if args.commit else "  (dry run — re-run with --commit)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_backfill_paradigms.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add scripts/backfill_paradigms.py tests/test_backfill_paradigms.py
git commit -m "feat(paradigm): backfill classifier (fail-loud on unmapped)"
```

---

### Task 4: Matchmaking within-paradigm guardrail

**Files:**

- Modify: `app/matchmaking.py` (`pick_pair` at :53, `pick_task` votable_count at :35)
- Test: `tests/test_matchmaking_paradigm.py`

**Interfaces:**

- Consumes: `Generator.paradigm` via `output.generator.paradigm`.
- Produces: `pick_pair` never returns two outputs whose generators differ in paradigm; `pick_task` treats a task as votable only if some paradigm group has ≥2 post-exclusion outputs. New helper `_paradigm_groups(outputs) -> dict[str, list[ModelOutput]]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_matchmaking_paradigm.py
from __future__ import annotations

from app import matchmaking
from app.models import Generator, ModelOutput, Task


def _out(gen_paradigm, n):
    g = Generator(slug=f"g{id(object())}", name="g", kind="model", paradigm=gen_paradigm)
    return ModelOutput(generator=g, n_comparisons=n, asset_path="x.glb", is_gold=False)


def test_pick_pair_never_crosses_paradigm():
    task = Task(title="t", prompt="p", category_id=1)
    # 1 image_recon + 2 procedural_llm — only the llm pair is valid
    task.outputs = [_out("image_recon", 0), _out("procedural_llm", 1), _out("procedural_llm", 2)]
    for _ in range(30):
        pair = matchmaking.pick_pair(None, task)
        assert pair is not None
        assert pair[0].generator.paradigm == pair[1].generator.paradigm == "procedural_llm"


def test_pick_pair_none_when_no_same_paradigm_pair():
    task = Task(title="t", prompt="p", category_id=1)
    task.outputs = [_out("image_recon", 0), _out("procedural_llm", 1)]
    assert matchmaking.pick_pair(None, task) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_matchmaking_paradigm.py -q`
Expected: FAIL — `pick_pair` currently returns a cross-paradigm pair / non-None.

- [ ] **Step 3: Write minimal implementation**

In `app/matchmaking.py`, add the grouping helper and rewrite the pair selection inside `pick_pair` (keep the exclude_fn + least-sampled + A/B randomization behavior, but scoped to one paradigm group):

```python
from collections import defaultdict  # add to imports at top


def _paradigm_groups(outputs: list[ModelOutput]) -> dict[str, list[ModelOutput]]:
    """Group outputs by their generator's paradigm value."""
    groups: dict[str, list[ModelOutput]] = defaultdict(list)
    for o in outputs:
        groups[o.generator.paradigm].append(o)
    return groups
```

Replace the body of `pick_pair` after the `exclude_fn` filter (from `if len(outputs) < 2:` onward) with:

```python
    groups = _paradigm_groups(outputs)
    pairable = [g for g in groups.values() if len(g) >= 2]
    if not pairable:
        return None
    # Choose the paradigm group holding the globally least-sampled output (preserve the
    # least-sampled-first fairness), then pick the two least-sampled within that group.
    group = min(pairable, key=lambda g: min(o.n_comparisons for o in g))
    group = list(group)
    random.shuffle(group)
    group.sort(key=lambda o: o.n_comparisons)
    a, b = group[0], group[1]
    if random.random() < 0.5:
        a, b = b, a
    return a, b
```

In `pick_task`, replace `votable_count` so it counts the largest same-paradigm group:

```python
    def votable_count(t: Task) -> int:
        outs = _real_outputs(t)
        if exclude_fn is not None:
            outs = [o for o in outs if not exclude_fn(o)]
        groups = _paradigm_groups(outs)
        return max((len(g) for g in groups.values()), default=0)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_matchmaking_paradigm.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Run the existing matchmaking suite (no regression)**

Run: `.venv/bin/python -m pytest tests/ -k matchmaking -q`
Expected: PASS. (Existing tests use generators with default paradigm "" → one group → behaves as before.)

- [ ] **Step 6: Commit**

```bash
git add app/matchmaking.py tests/test_matchmaking_paradigm.py
git commit -m "feat(paradigm): matchmaking pairs only within a paradigm"
```

---

### Task 5: Rating aggregation within-paradigm filter

**Files:**

- Modify: `app/service.py` (`_matches_for_scope` at :140-179)
- Test: `tests/test_paradigm_aggregation.py`

**Interfaces:**

- Consumes: `paradigms.same_paradigm`, `Generator.paradigm`.
- Produces: `_matches_for_scope` excludes any comparison whose two outputs' generators differ in paradigm (in addition to the existing gold/trust/ref-gen exclusions).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_paradigm_aggregation.py
from __future__ import annotations

from app import service
from app.database import SessionLocal, init_db
from app.models import (
    Category, Comparison, Criterion, Generator, ModelOutput, Task, Vote,
)


def setup_module(_m):
    init_db()


def _mk(db, paradigm):
    g = Generator(slug=f"agg-{paradigm}-{id(object())}", name="g", kind="model", paradigm=paradigm)
    db.add(g); db.flush()
    o = ModelOutput(task_id=db._task_id, generator_id=g.id, asset_path="x.glb", is_gold=False)
    db.add(o); db.flush()
    return g, o


def test_cross_paradigm_comparison_excluded_from_matches():
    with SessionLocal() as db:
        cat = Category(slug=f"c{id(object())}", name="c"); db.add(cat); db.flush()
        crit = db.execute(
            __import__("sqlalchemy").select(Criterion).where(Criterion.slug == "overall")
        ).scalars().first() or Criterion(slug="overall", name="Overall")
        if crit.id is None:
            db.add(crit); db.flush()
        t = Task(category_id=cat.id, title="t", prompt="p"); db.add(t); db.flush()
        db._task_id = t.id
        g1, o1 = _mk(db, "image_recon")
        g2, o2 = _mk(db, "procedural_llm")   # different paradigm
        g3, o3 = _mk(db, "image_recon")      # same as g1
        # cross-paradigm comparison (o1 vs o2) + within-paradigm (o1 vs o3)
        for a, b, key in [(o1, o2, "x1"), (o1, o3, "x2")]:
            comp = Comparison(task_id=t.id, output_a_id=a.id, output_b_id=b.id,
                              criterion_id=crit.id, session_id=key, is_gold=False)
            db.add(comp); db.flush()
            db.add(Vote(comparison_id=comp.id, winner="a", session_id=key)); db.flush()
        db.commit()
        matches = service._matches_for_scope(db, crit.id, None)
        pairs = set(matches)
        assert (g1.id, g3.id) in pairs          # within-paradigm kept
        assert (g1.id, g2.id) not in pairs       # cross-paradigm dropped
        assert (g2.id, g1.id) not in pairs
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_paradigm_aggregation.py -q`
Expected: FAIL — cross-paradigm pair `(g1, g2)` currently appears in matches.

- [ ] **Step 3: Write minimal implementation**

In `app/service.py`, add the import near the other app imports:

```python
from .paradigms import same_paradigm
```

In `_matches_for_scope`, inside the `for vote, comparison in ...` loop, after computing `gen_a`/`gen_b` and the existing `ref_gens` skip, add a paradigm check. Change:

```python
        gen_a = db.get(ModelOutput, comparison.output_a_id).generator_id
        gen_b = db.get(ModelOutput, comparison.output_b_id).generator_id
        if gen_a in ref_gens or gen_b in ref_gens:
            continue  # GT/reference scans are not perceptual competitors (Mode-A exclusion)
```

to:

```python
        out_a = db.get(ModelOutput, comparison.output_a_id)
        out_b = db.get(ModelOutput, comparison.output_b_id)
        gen_a = out_a.generator_id
        gen_b = out_b.generator_id
        if gen_a in ref_gens or gen_b in ref_gens:
            continue  # GT/reference scans are not perceptual competitors (Mode-A exclusion)
        if not same_paradigm(
            db.get(Generator, gen_a).paradigm, db.get(Generator, gen_b).paradigm
        ):
            continue  # never rank across paradigms
```

(Note: `Generator` is already imported in `app/service.py`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_paradigm_aggregation.py -q`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add app/service.py tests/test_paradigm_aggregation.py
git commit -m "feat(paradigm): drop cross-paradigm comparisons from rating aggregation"
```

---

### Task 6: Leaderboard + coverage UI (paradigm column + filter + facet)

**Files:**

- Modify: `app/main.py` (`_leaderboard_rows` :348, `/leaderboard` :444, `/api/leaderboard` :488)
- Modify: `app/service.py` (`coverage_summary` :514)
- Modify: `app/templates/leaderboard.html`, `app/templates/coverage.html`
- Test: `tests/test_leaderboard_paradigm.py`

**Interfaces:**

- Consumes: `Generator.paradigm`, `paradigms.DISPLAY_NAMES`.
- Produces: each leaderboard row dict carries `"paradigm"`; `_leaderboard_rows(..., paradigm: str | None = None)` filters to one paradigm when given; `coverage_summary` includes a `"by_paradigm"` count map.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_leaderboard_paradigm.py
from __future__ import annotations

from app import main as arena_main
from app import service
from app.database import SessionLocal, init_db
from app.models import Criterion, Generator, ModelOutput, Rating, Task, Category


def setup_module(_m):
    init_db()


def test_leaderboard_rows_carry_and_filter_paradigm():
    with SessionLocal() as db:
        crit = db.query(Criterion).filter_by(slug="overall").first() or Criterion(
            slug="overall", name="Overall"
        )
        if crit.id is None:
            db.add(crit); db.commit()
        made = []
        for pgm in ("image_recon", "procedural_llm"):
            g = Generator(slug=f"lb-{pgm}", name=pgm, kind="model", paradigm=pgm)
            db.add(g); db.flush()
            db.add(Rating(generator_id=g.id, criterion_id=crit.id, category_id=None,
                          bt_score=1000.0, bt_lower=990.0, bt_upper=1010.0, n_games=5))
            made.append(g.slug)
        db.commit()
        rows = arena_main._leaderboard_rows(db, "overall", None)
        pgms = {r["paradigm"] for r in rows if r["generator"] in ("image_recon", "procedural_llm")}
        assert {"image_recon", "procedural_llm"} <= pgms
        only = arena_main._leaderboard_rows(db, "overall", None, paradigm="procedural_llm")
        assert all(r["paradigm"] == "procedural_llm" for r in only)
        assert not any(r["paradigm"] == "image_recon" for r in only)


def test_coverage_summary_has_by_paradigm():
    with SessionLocal() as db:
        cov = service.coverage_summary(db)
        assert "by_paradigm" in cov and isinstance(cov["by_paradigm"], dict)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_leaderboard_paradigm.py -q`
Expected: FAIL — rows lack `"paradigm"`, `_leaderboard_rows` has no `paradigm` param, `coverage_summary` lacks `by_paradigm`.

- [ ] **Step 3: Write minimal implementation**

In `app/main.py` `_leaderboard_rows`, add the `paradigm` parameter and include it per row + filter:

```python
def _leaderboard_rows(
    db: Session,
    criterion_slug: str = "overall",
    category_slug: str | None = None,
    paradigm: str | None = None,
) -> list[dict]:
```

Inside the `for r in ratings:` loop, after `gen = db.get(Generator, r.generator_id)` and the `None` guard, add the filter + field:

```python
        if paradigm is not None and gen.paradigm != paradigm:
            continue
        # (existing rows.append below — add one key:)
```

and add `"paradigm": gen.paradigm,` to the appended row dict.

In the `/leaderboard` route (`:444`) and `/api/leaderboard` (`:488`), accept an optional `paradigm: str | None = None` query param and pass it through to `_leaderboard_rows(...)`. Pass the `DISPLAY_NAMES` map + the distinct paradigms present to the template context for the filter dropdown.

In `app/service.py` `coverage_summary`, add a `by_paradigm` count (non-gold outputs grouped by their generator's paradigm):

```python
    from .models import Generator  # if not already imported at top
    by_paradigm: dict[str, int] = {}
    for o in db.execute(select(ModelOutput).where(ModelOutput.is_gold.is_(False))).scalars():
        g = db.get(Generator, o.generator_id)
        key = g.paradigm if g else ""
        by_paradigm[key] = by_paradigm.get(key, 0) + 1
    # include by_paradigm in the returned dict
```

In `leaderboard.html`: add a "Paradigm" column to the table (render `row.paradigm` via the display-name map) and a filter control (a `<select>` posting `?paradigm=`). In `coverage.html`: render the `by_paradigm` counts as a facet list.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_leaderboard_paradigm.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Run full suite (no regression)**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (all prior + new tests).

- [ ] **Step 6: Commit**

```bash
git add app/main.py app/service.py app/templates/leaderboard.html app/templates/coverage.html tests/test_leaderboard_paradigm.py
git commit -m "feat(paradigm): paradigm column + filter on leaderboard & coverage"
```

---

## Operator step (after implementation, gated)

Backfill the study DB (snapshot first per the DB-change discipline):

```
# dry-run — inspect the generator->paradigm table, confirm 0 unmapped
BIO3D_DATABASE_URL=sqlite:///$(pwd)/data/study/arena-study.db .venv/bin/python scripts/backfill_paradigms.py
# then snapshot + commit
BIO3D_DATABASE_URL=... .venv/bin/python scripts/backfill_paradigms.py --commit
# recompute leaderboards so cross-paradigm votes drop out of ratings
BIO3D_DATABASE_URL=... .venv/bin/python -c "from app.database import SessionLocal; from app import service; \
  db=SessionLocal(); [service.recompute_leaderboard(db, c) for c in ('overall','visual_quality','structural_accuracy')]"
```

If the dry-run lists unmapped generators, add rules to `classify()` and re-run before `--commit`.

## Self-Review

- **Spec coverage:** §A data model → Task 2; vocabulary → Task 1; §B backfill (fail-loud) → Task 3; §C guardrail matchmaking → Task 4; §C guardrail rating aggregation → Task 5; §D minimal UI (column+filter+coverage facet) → Task 6; testing bullets distributed across tasks; real-execution backfill dry-run → Operator step. No gaps.
- **Placeholder scan:** none — every code step is complete.
- **Type consistency:** `same_paradigm(a, b)` (Task 1) used in Task 5; `_paradigm_groups(outputs) -> dict[str, list]` (Task 4); `_leaderboard_rows(..., paradigm=None)` and row `"paradigm"` key (Task 6) consistent; `classify`/`assign_paradigms` signatures (Task 3) match their test. `Generator.paradigm` (Task 2) consumed by Tasks 3–6.
