# Formal Difficulty Roster (v1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the thin recon-only difficulty scaffold into a formal multi-axis, per-taxon geometric-difficulty roster grounded in published hardness axes, applied across all paradigms, surfaced as a paradigm × tier grid.

**Architecture:** A pure rubric module (`app/difficulty_rubric.py`) scores 7 taxa over 5 grounded axes → tier. A new `TaxonDifficulty` side table is the per-taxon source of truth; a title→species resolver materializes it onto the existing per-task `TaskDifficulty`. A new `paradigm_tier_scorecard` aggregates the existing objective metrics by paradigm × tier for the `/difficulty` grid. Human Bradley-Terry path untouched.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 (create_all-only schema), Jinja2, pytest.

## Global Constraints

- **Read-only on the real study DB.** All scoring/materialize runs on a COPY. NEVER `BIO3D_DATABASE_URL=study`. Test runner `.venv/bin/pytest`.
- **Never touch the human Bradley-Terry / Elo path.** Aggregate only pre-computed objective metrics (`Metric`, `OrganMetric`); never recompute BT.
- **Fail-loud, no silent fallback.** Missing/invalid axis score, unknown taxon, or unparseable title → raise. A task whose species has no `TaxonDifficulty` is reported in `materialize`'s `skipped` list (not raised) and the **seeding script raises** if `skipped` is non-empty — fail-loud at the operational boundary, never a silent untiered drop.
- **Honor the create_all-only + side-table convention.** No `ALTER TABLE task`. New data → new side table (`create_all` picks it up).
- **Tier vocabulary stays exactly `{easy, moderate, hard}`.**
- **`tier_scorecard`'s existing (tier × generator) output stays behavior-identical.** The paradigm grid is additive.
- JSON columns are `Text` with a JSON string (project pattern: `Text, default="{}"`).

### Test-isolation convention (CRITICAL — the suite shares one DB engine)

`conftest.py` isolates the whole run into ONE temp-dir DB; there is **no per-test drop/recreate**. Rows only disappear if a test never commits (uncommitted flushes roll back on `Session` close) — see `tests/test_metric_model.py`. Therefore:

- **Functions that read GLOBAL aggregated state** (`tier_scorecard`, `paradigm_tier_scorecard` — both key off `TaskDifficulty`) must be tested with a `_clean(db)` that **deletes ALL `TaskDifficulty`** plus this test's own rows (scoped by a unique slug/title prefix and a unique `Metric.detail`/`OrganMetric.detail` tag), commits, then builds its own fixture and commits. Copy the exact shape of `tests/test_difficulty_scorecard.py`.
- **Cross-session tests** (`TestClient`, whose request opens a _separate_ `SessionLocal`) must **commit** their setup, so use the same `_clean` + unique-prefix + commit pattern.
- **Same-session unit tests** may use `SessionLocal()` + `db.flush()` (never `commit`) and pass `commit=False` to any function under test, so everything rolls back on close. **Assert on the test's own ids/slugs — never global row counts** (leaked committed rows from other test files are visible).

---

### Task 1: Rubric module (`app/difficulty_rubric.py`)

**Files:**

- Create: `app/difficulty_rubric.py`
- Test: `tests/test_difficulty_rubric.py`

**Interfaces:**

- Produces: `AXES: tuple[str, ...]` (5 keys); `tier_for_scores(scores: dict[str, int]) -> str`; `RUBRIC: dict[str, dict]` (7 taxa, each `{"scores": {axis:int}, "rationale": {axis:str}}`); `taxon_axes(species_slug) -> dict[str, int]`; `taxon_tier(species_slug) -> str`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_difficulty_rubric.py
import pytest

from app import difficulty_rubric as dr


def test_tier_thresholds_at_boundaries():
    base = {a: 0 for a in dr.AXES}
    assert dr.tier_for_scores({**base}) == "easy"  # sum 0
    assert dr.tier_for_scores({**base, "fine_detail": 2, "self_occlusion": 1}) == "easy"  # sum 3
    assert dr.tier_for_scores({**base, "fine_detail": 2, "self_occlusion": 2}) == "moderate"  # sum 4
    assert dr.tier_for_scores({**base, "fine_detail": 2, "self_occlusion": 2,
                               "non_rigidity": 2}) == "moderate"  # sum 6
    assert dr.tier_for_scores({"fine_detail": 2, "self_occlusion": 2, "non_rigidity": 2,
                               "topology": 1, "thin_structure": 0}) == "hard"  # sum 7
    assert dr.tier_for_scores({a: 2 for a in dr.AXES}) == "hard"  # sum 10


def test_tier_for_scores_fails_loud():
    good = {a: 1 for a in dr.AXES}
    with pytest.raises(ValueError):
        dr.tier_for_scores({a: 1 for a in dr.AXES[:-1]})  # missing axis
    with pytest.raises(ValueError):
        dr.tier_for_scores({**good, "bogus": 1})  # unknown key
    with pytest.raises(ValueError):
        dr.tier_for_scores({**good, "topology": 3})  # out of range
    with pytest.raises(ValueError):
        dr.tier_for_scores({**good, "topology": -1})


def test_rubric_complete_and_scored():
    expected = {
        "solanum_lycopersicum": "easy",
        "zea_mays": "moderate",
        "glycine_max": "moderate",
        "arabidopsis_thaliana": "hard",
        "pinus_sylvestris": "hard",
        "rosa": "hard",
        "hordeum_vulgare": "hard",
    }
    assert set(dr.RUBRIC) == set(expected)
    for slug, entry in dr.RUBRIC.items():
        assert set(entry["scores"]) == set(dr.AXES), slug
        assert set(entry["rationale"]) == set(dr.AXES), slug
        assert all(isinstance(v, int) and 0 <= v <= 2 for v in entry["scores"].values()), slug
        assert dr.taxon_tier(slug) == expected[slug]


def test_taxon_lookups_fail_loud():
    with pytest.raises(ValueError):
        dr.taxon_tier("unknown_species")
    with pytest.raises(ValueError):
        dr.taxon_axes("unknown_species")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_difficulty_rubric.py -q`
Expected: FAIL (`ModuleNotFoundError: app.difficulty_rubric`).

- [ ] **Step 3: Write the module**

```python
# app/difficulty_rubric.py
"""Multi-axis geometric-difficulty rubric for benchmark taxa.

Grounded axes: fine_detail (Dora-Bench salient-edge-density, arXiv 2412.17808),
self_occlusion + non_rigidity (Yunus et al., "Recent Trends in 3D Reconstruction of
General Non-Rigid Scenes", CGF 2024, arXiv 2403.15064), and topology + thin_structure
(ours, unclaimed for organisms). Each axis is scored 0..2; the sum maps to a difficulty
tier. Pure module — no DB, exhaustively unit-tested. Difficulty is a property of the
TAXON (not a specimen mesh); v1 scores are hand-authored against the cited axis
definitions. Computed corroboration of the computable axes is a deferred follow-on.
"""

from __future__ import annotations

AXES: tuple[str, ...] = (
    "fine_detail",
    "self_occlusion",
    "non_rigidity",
    "topology",
    "thin_structure",
)

# Sum cut points (inclusive) → tier. Max sum = 2 * len(AXES) = 10.
_EASY_MAX = 3
_MODERATE_MAX = 6


def tier_for_scores(scores: dict[str, int]) -> str:
    """Validate all AXES present and each an int in 0..2, sum, map to a tier. Fail-loud."""
    missing = [a for a in AXES if a not in scores]
    if missing:
        raise ValueError(f"missing axis scores: {missing}")
    extra = [a for a in scores if a not in AXES]
    if extra:
        raise ValueError(f"unknown axis keys: {extra}")
    for a in AXES:
        v = scores[a]
        if not isinstance(v, int) or v < 0 or v > 2:
            raise ValueError(f"axis {a!r} score must be an int in 0..2, got {v!r}")
    total = sum(scores[a] for a in AXES)
    if total <= _EASY_MAX:
        return "easy"
    if total <= _MODERATE_MAX:
        return "moderate"
    return "hard"


# Hand-authored v1 scores + rationale per in-scope taxon (species_slug matches
# ReconTask.species_slug / the title binomial). Comment shows the tier + sum.
RUBRIC: dict[str, dict] = {
    "solanum_lycopersicum": {  # sum 3 → easy
        "scores": {"fine_detail": 1, "self_occlusion": 1, "non_rigidity": 1,
                   "topology": 0, "thin_structure": 0},
        "rationale": {
            "fine_detail": "large leaves and fruit dominate; only modest leaf serration",
            "self_occlusion": "moderate leaf overlap on a bushy but open habit",
            "non_rigidity": "leaves flex but the compact plant is self-supporting",
            "topology": "single connected bush, few through-holes",
            "thin_structure": "thick stems, broad leaves, large fruit — no fine filaments",
        },
    },
    "zea_mays": {  # sum 4 → moderate
        "scores": {"fine_detail": 1, "self_occlusion": 1, "non_rigidity": 1,
                   "topology": 0, "thin_structure": 1},
        "rationale": {
            "fine_detail": "tassel and silk carry fine detail over broad blade leaves",
            "self_occlusion": "arching blade leaves overlap moderately",
            "non_rigidity": "long blade leaves bend and curl",
            "topology": "single stalk, connected",
            "thin_structure": "thin tassel/silk and blade-leaf edges",
        },
    },
    "glycine_max": {  # sum 6 → moderate
        "scores": {"fine_detail": 1, "self_occlusion": 2, "non_rigidity": 1,
                   "topology": 1, "thin_structure": 1},
        "rationale": {
            "fine_detail": "trifoliate leaves and pods; pubescence is fine but not dominant",
            "self_occlusion": "dense bushy canopy with heavily overlapping leaves",
            "non_rigidity": "broad leaflets flop on thin petioles",
            "topology": "branching stems add moderate branch complexity",
            "thin_structure": "thin petioles and stems, pod edges",
        },
    },
    "arabidopsis_thaliana": {  # sum 7 → hard
        "scores": {"fine_detail": 2, "self_occlusion": 1, "non_rigidity": 1,
                   "topology": 1, "thin_structure": 2},
        "rationale": {
            "fine_detail": "small rosette leaves plus tiny siliques and flowers",
            "self_occlusion": "flat rosette is fairly open; bolting stems mostly exposed",
            "non_rigidity": "thin bolting stems flex",
            "topology": "branching inflorescence adds branch complexity",
            "thin_structure": "very thin bolting stems and small parts",
        },
    },
    "pinus_sylvestris": {  # sum 7 → hard
        "scores": {"fine_detail": 2, "self_occlusion": 2, "non_rigidity": 0,
                   "topology": 1, "thin_structure": 2},
        "rationale": {
            "fine_detail": "thousands of fine needles — extreme repeated detail",
            "self_occlusion": "dense needle and branch canopy, heavy occlusion",
            "non_rigidity": "woody and rigid — needles/branches barely deform",
            "topology": "branching woody structure",
            "thin_structure": "needles are the definitional thin structure",
        },
    },
    "rosa": {  # sum 7 → hard
        "scores": {"fine_detail": 2, "self_occlusion": 2, "non_rigidity": 1,
                   "topology": 1, "thin_structure": 1},
        "rationale": {
            "fine_detail": "layered petals, serrated leaflets, and thorns",
            "self_occlusion": "dense bushy shrub; petals occlude the flower interior",
            "non_rigidity": "leaves and blooms flex",
            "topology": "branching canes with multiple blooms",
            "thin_structure": "thorns and thin stems over moderate woody canes",
        },
    },
    "hordeum_vulgare": {  # sum 9 → hard  (root-system MRI task)
        "scores": {"fine_detail": 2, "self_occlusion": 2, "non_rigidity": 1,
                   "topology": 2, "thin_structure": 2},
        "rationale": {
            "fine_detail": "fine lateral roots and root hairs",
            "self_occlusion": "dense root network crossing and overlapping in soil",
            "non_rigidity": "roots are flexible though the MRI capture is static",
            "topology": "highly branching network — many branches / high genus",
            "thin_structure": "roots are thin filamentous structures throughout",
        },
    },
}


def taxon_axes(species_slug: str) -> dict[str, int]:
    """The 0..2 axis scores for a taxon. Fail-loud on unknown taxon."""
    entry = RUBRIC.get(species_slug)
    if entry is None:
        raise ValueError(f"no rubric entry for taxon {species_slug!r}")
    return dict(entry["scores"])


def taxon_tier(species_slug: str) -> str:
    """The difficulty tier for a taxon (validates its scores). Fail-loud on unknown taxon."""
    return tier_for_scores(taxon_axes(species_slug))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_difficulty_rubric.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add app/difficulty_rubric.py tests/test_difficulty_rubric.py
git commit -m "feat(difficulty): multi-axis geometric-difficulty rubric (7 taxa, cited axes)"
```

---

### Task 2: `TaxonDifficulty` side table (`app/models.py`)

**Files:**

- Modify: `app/models.py` (add class immediately after `TaskDifficulty`, which ends at line 514, before `class PlantMorphology`)
- Test: `tests/test_taxon_difficulty_model.py`

**Interfaces:**

- Produces: `TaxonDifficulty` — `id`, `species_slug` (unique, indexed), `tier`, `axis_scores` (JSON text), `rationale` (JSON text), `updated`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_taxon_difficulty_model.py
import json

from sqlalchemy import select

from app.database import SessionLocal, init_db
from app.models import TaxonDifficulty


def setup_module(_m):
    init_db()


def test_taxon_difficulty_roundtrip():
    # flush (not commit) → rolls back on close; assert within the same session.
    with SessionLocal() as db:
        db.add(TaxonDifficulty(
            species_slug="rosa", tier="hard",
            axis_scores=json.dumps({"fine_detail": 2}),
            rationale=json.dumps({"fine_detail": "layered petals"}),
        ))
        db.flush()
        row = db.execute(
            select(TaxonDifficulty).where(TaxonDifficulty.species_slug == "rosa")
        ).scalars().one()
        assert row.tier == "hard"
        assert json.loads(row.axis_scores)["fine_detail"] == 2
        assert row.updated is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_taxon_difficulty_model.py -q`
Expected: FAIL (`ImportError: cannot import name 'TaxonDifficulty'`).

- [ ] **Step 3: Add the model** — insert after `TaskDifficulty` (after line 514, before `class PlantMorphology`):

```python
class TaxonDifficulty(Base):
    """Per-taxon geometric-difficulty tier — the source of truth that TaskDifficulty is
    materialized from. Separate table (not a Task/ReconTask column) to honor the
    create_all-only schema — mirrors TaskDifficulty/PlantMorphology. axis_scores and
    rationale are JSON text keyed by difficulty_rubric.AXES; tier ∈ difficulty.TIERS."""

    __tablename__ = "taxon_difficulty"
    __table_args__ = (UniqueConstraint("species_slug", name="uq_taxon_difficulty_species"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    species_slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    tier: Mapped[str] = mapped_column(String(16))
    axis_scores: Mapped[str] = mapped_column(Text, default="{}")
    rationale: Mapped[str] = mapped_column(Text, default="{}")
    updated: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)
```

(All names — `Base`, `Mapped`, `mapped_column`, `String`, `Text`, `DateTime`, `UniqueConstraint`, `_utcnow` — are already imported/defined in `app/models.py`. A brand-new table is created by `create_all`; no `_ensure_columns` change is needed.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_taxon_difficulty_model.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/models.py tests/test_taxon_difficulty_model.py
git commit -m "feat(difficulty): TaxonDifficulty side table (per-taxon source of truth)"
```

---

### Task 3: Task→taxon resolver + materialize (`app/difficulty.py`)

**Files:**

- Modify: `app/difficulty.py` (add two functions after `set_task_difficulty`, which ends line 39)
- Test: `tests/test_difficulty_materialize.py`

**Interfaces:**

- Consumes: `TaxonDifficulty` (Task 2), `set_task_difficulty` (existing).
- Produces: `species_slug_for_task(task) -> str`; `materialize_task_difficulty(db, commit=True) -> {"materialized": int, "skipped": list[tuple[int, str]], "taxa": int}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_difficulty_materialize.py
import json

import pytest
from sqlalchemy import select

from app import difficulty
from app.database import SessionLocal, init_db
from app.models import Category, ReconTask, Task, TaskDifficulty, TaxonDifficulty


def setup_module(_m):
    init_db()


def _mk_task(db, title, cat):
    t = Task(category_id=cat.id, title=title, prompt="p")
    db.add(t)
    db.flush()
    return t


def _seed(db, prefix):
    """flush-only fixture (rolls back on close). Unique category slug per test."""
    cat = Category(slug=f"{prefix}-cat", name="Plants")
    db.add(cat)
    db.flush()
    t_recon = _mk_task(db, "Rosa — single-image → 3D reconstruction", cat)
    t_bot = _mk_task(db, "Rosa — botanical plausibility", cat)
    t_zea = _mk_task(db, "Zea mays — single-image → 3D reconstruction", cat)
    db.add(ReconTask(task_id=t_recon.id, species_slug="rosa", species_name="Rose"))
    db.add(TaxonDifficulty(species_slug="rosa", tier="hard",
                           axis_scores=json.dumps({}), rationale=json.dumps({})))
    db.add(TaxonDifficulty(species_slug="zea_mays", tier="moderate",
                           axis_scores=json.dumps({}), rationale=json.dumps({})))
    db.flush()
    return t_recon, t_bot, t_zea


def test_species_slug_for_task_parses_binomial():
    with SessionLocal() as db:
        t_recon, t_bot, t_zea = _seed(db, "dmp1")
        assert difficulty.species_slug_for_task(t_recon) == "rosa"
        assert difficulty.species_slug_for_task(t_bot) == "rosa"
        assert difficulty.species_slug_for_task(t_zea) == "zea_mays"


def test_species_slug_matches_recon_task():
    with SessionLocal() as db:
        t_recon, _, _ = _seed(db, "dmp2")
        rt = db.execute(select(ReconTask).where(ReconTask.task_id == t_recon.id)).scalars().one()
        assert difficulty.species_slug_for_task(t_recon) == rt.species_slug


def test_species_slug_fails_loud_on_empty_title():
    with SessionLocal() as db:
        cat = Category(slug="dmp3-cat", name="X")
        db.add(cat)
        db.flush()
        t = _mk_task(db, "— nothing before the dash", cat)
        with pytest.raises(ValueError):
            difficulty.species_slug_for_task(t)


def test_materialize_projects_taxon_onto_all_tasks():
    with SessionLocal() as db:
        t_recon, t_bot, t_zea = _seed(db, "dmp4")
        res = difficulty.materialize_task_difficulty(db, commit=False)
        covered = {t_recon.id, t_bot.id, t_zea.id}
        by_task = {td.task_id: td.tier
                   for td in db.execute(select(TaskDifficulty)).scalars()
                   if td.task_id in covered}
        assert by_task[t_recon.id] == "hard"
        assert by_task[t_bot.id] == "hard"   # second rosa task inherits the SAME tier
        assert by_task[t_zea.id] == "moderate"
        assert res["materialized"] >= 3      # my 3 (+ any leaked covered tasks)
        # idempotent: re-run, still exactly one TaskDifficulty row per covered task
        difficulty.materialize_task_difficulty(db, commit=False)
        for tid in covered:
            rows = [td for td in db.execute(select(TaskDifficulty)).scalars()
                    if td.task_id == tid]
            assert len(rows) == 1


def test_materialize_reports_uncovered_in_skipped():
    with SessionLocal() as db:
        cat = Category(slug="dmp5-cat", name="Plants")
        db.add(cat)
        db.flush()
        t = _mk_task(db, "Cucumis sativus — single-image → 3D reconstruction", cat)  # no TaxonDifficulty
        res = difficulty.materialize_task_difficulty(db, commit=False)
        assert (t.id, "cucumis_sativus") in res["skipped"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_difficulty_materialize.py -q`
Expected: FAIL (`AttributeError: ... 'species_slug_for_task'`).

- [ ] **Step 3: Add the functions to `app/difficulty.py`** (after `set_task_difficulty`, line 39):

```python
def species_slug_for_task(task) -> str:
    """Resolve a task's taxon slug from its title's binomial prefix:
    'Rosa — single-image → 3D reconstruction' → 'rosa',
    'Zea mays — botanical plausibility' → 'zea_mays'. Matches ReconTask.species_slug.
    Fail-loud if the title yields no slug."""
    head = task.title.split("—")[0].strip()
    slug = head.lower().replace(" ", "_")
    if not slug:
        raise ValueError(f"task {task.id} title yields no species slug: {task.title!r}")
    return slug


def materialize_task_difficulty(db, commit: bool = True) -> dict:
    """Project TaxonDifficulty onto per-task TaskDifficulty rows via species_slug_for_task.
    Idempotent (set_task_difficulty upserts by task_id). A task whose resolved species has no
    TaxonDifficulty row is collected into `skipped` (NOT raised) — the seeding script enforces
    fail-loud on a non-empty skipped. commit=False lets tests run under transaction rollback."""
    from .models import Task, TaxonDifficulty

    taxon = {t.species_slug: t for t in db.execute(select(TaxonDifficulty)).scalars()}
    materialized = 0
    skipped: list[tuple[int, str]] = []
    for task in db.execute(select(Task)).scalars():
        slug = species_slug_for_task(task)
        td = taxon.get(slug)
        if td is None:
            skipped.append((task.id, slug))
            continue
        set_task_difficulty(
            db, task.id, td.tier,
            rationale=f"taxon {slug}: {td.tier} (see TaxonDifficulty)", commit=False,
        )
        materialized += 1
    if commit:
        db.commit()
    return {"materialized": materialized, "skipped": skipped, "taxa": len(taxon)}
```

(`select` is already imported at the top of `app/difficulty.py`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_difficulty_materialize.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add app/difficulty.py tests/test_difficulty_materialize.py
git commit -m "feat(difficulty): title→species resolver + per-taxon materialize (skip-and-report)"
```

---

### Task 4: Paradigm × tier scorecard (`app/difficulty.py`)

**Files:**

- Modify: `app/difficulty.py` (add `paradigm_tier_scorecard` at end of file)
- Test: `tests/test_paradigm_tier_scorecard.py`

**Interfaces:**

- Consumes: existing `Metric`, `OrganMetric`, `ModelOutput`, `Generator`, `TaskDifficulty`; `app.paradigms.PARADIGMS` + `DISPLAY_NAMES`.
- Produces: `paradigm_tier_scorecard(db) -> list[dict]` — `[{"tier": str, "rows": [{"paradigm","paradigm_display","n_outputs","n_scored","mean_chamfer","mean_fscore","mean_structural","species_pass_rate"}]}]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_paradigm_tier_scorecard.py
from app.database import SessionLocal, init_db
from app.difficulty import paradigm_tier_scorecard, set_task_difficulty, tier_scorecard
from app.models import (
    Category, Generator, Metric, ModelOutput, Task, TaskDifficulty,
)


def setup_module(_m):
    init_db()


def _clean(db):
    # paradigm_tier_scorecard reads global TaskDifficulty → this test must own tier state fully.
    db.query(TaskDifficulty).delete()
    db.query(Metric).filter(Metric.detail == "pts").delete(synchronize_session=False)
    db.query(ModelOutput).filter(ModelOutput.asset_path.like("pts/%.glb")).delete(
        synchronize_session=False
    )
    db.query(Task).filter(Task.title.like("pts-%")).delete(synchronize_session=False)
    db.query(Generator).filter(Generator.slug.like("pts-%")).delete(synchronize_session=False)
    db.query(Category).filter_by(slug="pts-cat").delete(synchronize_session=False)
    db.commit()


def test_paradigm_grid_groups_by_paradigm_and_tier():
    with SessionLocal() as db:
        _clean(db)
        cat = Category(slug="pts-cat", name="C")
        g_recon = Generator(slug="pts-recon", name="Recon", paradigm="image_recon")
        g_proc = Generator(slug="pts-proc", name="Proc", paradigm="procedural_llm")
        g_bare = Generator(slug="pts-bare", name="Bare", paradigm="")  # empty → 'unspecified'
        db.add_all([cat, g_recon, g_proc, g_bare])
        db.flush()
        hard = Task(category_id=cat.id, title="pts-hard", prompt="p")
        db.add(hard)
        db.flush()
        o1 = ModelOutput(task_id=hard.id, generator_id=g_recon.id, asset_path="pts/1.glb")
        o2 = ModelOutput(task_id=hard.id, generator_id=g_recon.id, asset_path="pts/2.glb")
        o3 = ModelOutput(task_id=hard.id, generator_id=g_proc.id, asset_path="pts/3.glb")
        o4 = ModelOutput(task_id=hard.id, generator_id=g_bare.id, asset_path="pts/4.glb")  # unscored
        db.add_all([o1, o2, o3, o4])
        db.flush()
        db.add_all([
            Metric(output_id=o1.id, chamfer=0.2, fscore=0.6, species_verdict="PASS", detail="pts"),
            Metric(output_id=o2.id, chamfer=0.3, fscore=0.8, species_verdict="PASS", detail="pts"),
            Metric(output_id=o3.id, chamfer=0.1, fscore=0.9, species_verdict="PASS", detail="pts"),
            # o4 has no Metric (unscored) → counts toward n_outputs, not n_scored.
        ])
        set_task_difficulty(db, hard.id, "hard", "occlusion", commit=False)
        db.commit()

        card = paradigm_tier_scorecard(db)
        assert [b["tier"] for b in card] == ["easy", "moderate", "hard", "untiered"]
        hard_b = next(b for b in card if b["tier"] == "hard")
        rows = {r["paradigm"]: r for r in hard_b["rows"]}
        assert rows["image_recon"]["n_outputs"] == 2
        assert rows["image_recon"]["n_scored"] == 2
        assert abs(rows["image_recon"]["mean_chamfer"] - 0.25) < 1e-9
        assert rows["image_recon"]["paradigm_display"] == "Image→3D reconstruction"
        assert rows["image_recon"]["species_pass_rate"] == 1.0
        assert rows["procedural_llm"]["n_outputs"] == 1
        assert rows["unspecified"]["n_outputs"] == 1
        assert rows["unspecified"]["mean_chamfer"] is None  # unscored → None, never zero


def test_tier_scorecard_shape_regression():
    # the generator-level scorecard must still return the documented shape
    with SessionLocal() as db:
        card = tier_scorecard(db)
        assert [b["tier"] for b in card] == ["easy", "moderate", "hard", "untiered"]
        assert all(set(b) == {"tier", "rows"} for b in card)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_paradigm_tier_scorecard.py -q`
Expected: FAIL (`AttributeError: ... 'paradigm_tier_scorecard'`).

- [ ] **Step 3: Add the function to `app/difficulty.py`** (end of file):

```python
def paradigm_tier_scorecard(db) -> list[dict]:
    """Per-(tier × paradigm) aggregate of the existing objective metrics — the headline
    cross-paradigm × difficulty grid. Same objective-metric plumbing as tier_scorecard,
    grouped by Generator.paradigm instead of generator. Means skip None (never zero-fill);
    canonical tier order + 'untiered' bucket; empty-paradigm generators bucket under
    'unspecified'. Never recomputes Bradley-Terry; the human path is untouched."""
    from . import paradigms
    from .models import Generator, Metric, ModelOutput, OrganMetric

    tier_by_task = {td.task_id: td.tier for td in db.execute(select(TaskDifficulty)).scalars()}
    paradigm_by_gen = {g.id: (g.paradigm or "") for g in db.execute(select(Generator)).scalars()}
    chamfer_by_out, fscore_by_out, verdict_by_out = {}, {}, {}
    for m in db.execute(select(Metric)).scalars():
        chamfer_by_out[m.output_id] = m.chamfer
        fscore_by_out[m.output_id] = m.fscore
        verdict_by_out[m.output_id] = m.species_verdict
    structural_by_out = {
        om.output_id: om.botanical_fidelity for om in db.execute(select(OrganMetric)).scalars()
    }

    acc: dict[tuple[str, str], dict] = {}
    for out in db.execute(select(ModelOutput).where(ModelOutput.is_gold.is_(False))).scalars():
        tier = tier_by_task.get(out.task_id, "untiered")
        pgm = paradigm_by_gen.get(out.generator_id, "") or "unspecified"
        a = acc.setdefault(
            (tier, pgm),
            {"n_outputs": 0, "n_scored": 0, "chamfer": [], "fscore": [],
             "structural": [], "verdicts": []},
        )
        a["n_outputs"] += 1
        if out.id in chamfer_by_out:
            a["n_scored"] += 1
            if chamfer_by_out[out.id] is not None:
                a["chamfer"].append(chamfer_by_out[out.id])
            if fscore_by_out[out.id] is not None:
                a["fscore"].append(fscore_by_out[out.id])
            if verdict_by_out[out.id] is not None:
                a["verdicts"].append(verdict_by_out[out.id])
        if structural_by_out.get(out.id) is not None:
            a["structural"].append(structural_by_out[out.id])

    pgm_order = list(paradigms.PARADIGMS) + ["unspecified"]
    card = []
    for tier in list(TIERS) + ["untiered"]:
        rows = []
        for pgm in pgm_order:
            a = acc.get((tier, pgm))
            if not a:
                continue
            verdicts = a["verdicts"]
            pass_rate = (
                sum(1 for v in verdicts if v == "PASS") / len(verdicts) if verdicts else None
            )
            rows.append(
                {
                    "paradigm": pgm,
                    "paradigm_display": paradigms.DISPLAY_NAMES.get(pgm, pgm),
                    "n_outputs": a["n_outputs"],
                    "n_scored": a["n_scored"],
                    "mean_chamfer": _mean(a["chamfer"]),
                    "mean_fscore": _mean(a["fscore"]),
                    "mean_structural": _mean(a["structural"]),
                    "species_pass_rate": pass_rate,
                }
            )
        card.append({"tier": tier, "rows": rows})
    return card
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_paradigm_tier_scorecard.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add app/difficulty.py tests/test_paradigm_tier_scorecard.py
git commit -m "feat(difficulty): paradigm × tier objective scorecard (cross-paradigm grid)"
```

---

### Task 5: Seeding driver (`scripts/assign_difficulty.py` rewrite)

**Files:**

- Modify (rewrite): `scripts/assign_difficulty.py`
- Test: `tests/test_assign_difficulty_seed.py`

**Note:** the existing `tests/test_assign_difficulty.py` imports `assign_all` from this script — that symbol is removed here, so **delete `tests/test_assign_difficulty.py`** as part of this task (its behavior is superseded by the new seed/materialize flow). Confirm nothing else imports `assign_all`: `grep -rn "assign_all" app tests scripts` should return nothing after the rewrite.

**Interfaces:**

- Consumes: `difficulty_rubric.RUBRIC` + `tier_for_scores` (Task 1); `TaxonDifficulty` (Task 2); `materialize_task_difficulty` (Task 3); `config.is_safe_test_db_target` + `config.DATABASE_URL`.
- Produces: `seed_taxon_difficulty(db, rubric=None, commit=True) -> {"seeded": int}`; `main() -> int`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_assign_difficulty_seed.py
import json

from sqlalchemy import select

from app.database import SessionLocal, init_db
from app.difficulty_rubric import RUBRIC
from app.models import TaxonDifficulty
from scripts import assign_difficulty


def setup_module(_m):
    init_db()


def test_seed_taxon_difficulty_from_rubric():
    with SessionLocal() as db:  # commit=False → rolls back on close
        res = assign_difficulty.seed_taxon_difficulty(db, commit=False)
        assert res["seeded"] == 7
        rows = {r.species_slug: r for r in db.execute(select(TaxonDifficulty)).scalars()
                if r.species_slug in RUBRIC}
        assert rows["solanum_lycopersicum"].tier == "easy"
        assert rows["hordeum_vulgare"].tier == "hard"
        assert json.loads(rows["pinus_sylvestris"].axis_scores)["thin_structure"] == 2
        # idempotent — re-seed, still exactly 7 rubric rows (upsert by slug)
        assign_difficulty.seed_taxon_difficulty(db, commit=False)
        rubric_rows = [r for r in db.execute(select(TaxonDifficulty)).scalars()
                       if r.species_slug in RUBRIC]
        assert len(rubric_rows) == 7
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_assign_difficulty_seed.py -q`
Expected: FAIL (`AttributeError: ... 'seed_taxon_difficulty'`).

- [ ] **Step 3: Rewrite `scripts/assign_difficulty.py`**

```python
"""Seed TaxonDifficulty from difficulty_rubric.RUBRIC, then materialize per-task
TaskDifficulty rows. Idempotent. Refuses to run against a non-copy (study/prod) DB, and
fail-loud if any task's species has no rubric coverage.

Usage: .venv/bin/python scripts/assign_difficulty.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app import config, difficulty  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.difficulty_rubric import RUBRIC, tier_for_scores  # noqa: E402
from app.models import TaxonDifficulty  # noqa: E402


def seed_taxon_difficulty(db, rubric: dict | None = None, commit: bool = True) -> dict:
    """Upsert one TaxonDifficulty row per taxon in the rubric. Fail-loud on bad scores
    (via tier_for_scores). Idempotent (upsert by species_slug). commit=False for tests."""
    rubric = RUBRIC if rubric is None else rubric
    seeded = 0
    for slug, entry in rubric.items():
        tier = tier_for_scores(entry["scores"])
        row = (
            db.execute(select(TaxonDifficulty).where(TaxonDifficulty.species_slug == slug))
            .scalars()
            .first()
        )
        if row is None:
            row = TaxonDifficulty(species_slug=slug)
            db.add(row)
        row.tier = tier
        row.axis_scores = json.dumps(entry["scores"])
        row.rationale = json.dumps(entry["rationale"])
        seeded += 1
    if commit:
        db.commit()
    return {"seeded": seeded}


def main() -> int:
    if not config.is_safe_test_db_target(config.DATABASE_URL):
        # Seeding mutates TaxonDifficulty/TaskDifficulty. Never run against the real study DB;
        # point BIO3D_DATABASE_URL at a copy.
        raise SystemExit(
            "refusing to run against a non-copy DB — is_safe_test_db_target False; use a copy"
        )
    with SessionLocal() as db:
        seed = seed_taxon_difficulty(db)
        result = difficulty.materialize_task_difficulty(db)
    if result["skipped"]:
        # fail-loud at the operational boundary: a task with no rubric coverage.
        raise SystemExit(
            f"uncovered tasks (no TaxonDifficulty for their species): {result['skipped']}"
        )
    print({**seed, "materialized": result["materialized"], "taxa": result["taxa"]})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_assign_difficulty_seed.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git rm tests/test_assign_difficulty.py
git add scripts/assign_difficulty.py tests/test_assign_difficulty_seed.py
git commit -m "feat(difficulty): seed TaxonDifficulty from rubric + materialize (study-DB-guarded)"
```

---

### Task 6: Paradigm-grid view (`app/main.py` route + `/api/difficulty.json` + template)

**Files:**

- Modify: `app/main.py` (`difficulty_page` context + `api_difficulty` body)
- Modify: `app/templates/difficulty.html` (add a paradigm × tier section)
- Test: `tests/test_difficulty_view.py`

**Interfaces:**

- Consumes: `difficulty.paradigm_tier_scorecard` (Task 4).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_difficulty_view.py
from fastapi.testclient import TestClient

from app.database import SessionLocal, init_db
from app.difficulty import set_task_difficulty
from app.main import app
from app.models import (
    Category, Generator, Metric, ModelOutput, Task, TaskDifficulty,
)


def setup_module(_m):
    init_db()


def _clean(db):
    db.query(TaskDifficulty).delete()
    db.query(Metric).filter(Metric.detail == "dvw").delete(synchronize_session=False)
    db.query(ModelOutput).filter(ModelOutput.asset_path.like("dvw/%.glb")).delete(
        synchronize_session=False
    )
    db.query(Task).filter(Task.title.like("dvw-%")).delete(synchronize_session=False)
    db.query(Generator).filter(Generator.slug.like("dvw-%")).delete(synchronize_session=False)
    db.query(Category).filter_by(slug="dvw-cat").delete(synchronize_session=False)
    db.commit()


def _setup():
    # cross-session (TestClient opens its own session) → must COMMIT.
    with SessionLocal() as db:
        _clean(db)
        cat = Category(slug="dvw-cat", name="C")
        g = Generator(slug="dvw-recon", name="Recon", paradigm="image_recon")
        db.add_all([cat, g])
        db.flush()
        t = Task(category_id=cat.id, title="dvw-hard", prompt="p")
        db.add(t)
        db.flush()
        o = ModelOutput(task_id=t.id, generator_id=g.id, asset_path="dvw/1.glb")
        db.add(o)
        db.flush()
        db.add(Metric(output_id=o.id, chamfer=0.2, fscore=0.6, species_verdict="PASS", detail="dvw"))
        set_task_difficulty(db, t.id, "hard", "occlusion", commit=False)
        db.commit()


def test_api_difficulty_includes_paradigm_grid():
    _setup()
    data = TestClient(app).get("/api/difficulty.json").json()
    assert "scorecard" in data  # existing key preserved
    assert "paradigm_grid" in data
    hard = next(b for b in data["paradigm_grid"] if b["tier"] == "hard")
    assert any(r["paradigm"] == "image_recon" for r in hard["rows"])


def test_difficulty_page_renders_paradigm_grid():
    _setup()
    html = TestClient(app).get("/difficulty").text
    assert "Paradigm × difficulty" in html
    assert "Image→3D reconstruction" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_difficulty_view.py -q`
Expected: FAIL (`KeyError: 'paradigm_grid'` / missing heading).

- [ ] **Step 3a: Extend the route context** — in `app/main.py`, `difficulty_page`, add after `trait_tiers = service.tier_trait_accuracy(db)` (line 1150):

```python
    paradigm_grid = difficulty.paradigm_tier_scorecard(db)
```

Then add to the template context dict, after `"trait_tiers": trait_tiers,` (line 1161):

```python
            "paradigm_grid": paradigm_grid,
```

- [ ] **Step 3b: Extend the JSON endpoint** — replace the body of `api_difficulty` (starts line ~1168):

```python
@app.get("/api/difficulty.json")
def api_difficulty(db: Session = Depends(get_db)):
    """Per-tier objective scorecard (× generator and × paradigm) over existing metrics."""
    return {
        "scorecard": difficulty.tier_scorecard(db),
        "paradigm_grid": difficulty.paradigm_tier_scorecard(db),
    }
```

- [ ] **Step 3c: Add the template section** — in `app/templates/difficulty.html`, insert after the cross-tier gradient block (after line 40, the `{% endif %}` that closes `{% if gradient %}`), before the `{% if perceptual %}` block:

```html
{% if paradigm_grid %}
<h3>
  Paradigm × difficulty
  <span class="subtle"
    >— which approach wins at each hardness level (objective metrics)</span
  >
</h3>
<p class="subtle">
  The headline grid: mean objective metrics per <b>paradigm</b> per tier.
  Reconstruction is expected to degrade easy → hard while procedural code-gen
  holds or improves — the cross-paradigm gradient no other benchmark reports.
</p>
{% for block in paradigm_grid %} {% if block.tier in tiers and block.rows %}
<h4>{{ block.tier | capitalize }} tier</h4>
<table class="ranktable compact">
  <thead>
    <tr>
      <th>Paradigm</th>
      <th>n</th>
      <th>scored</th>
      <th>Chamfer ↓</th>
      <th>F-score ↑</th>
      <th>Structural ↑</th>
      <th>Species pass-rate</th>
    </tr>
  </thead>
  <tbody>
    {% for r in block.rows %}
    <tr>
      <td class="gen">{{ r.paradigm_display }}</td>
      <td class="num">{{ r.n_outputs }}</td>
      <td class="num">{{ r.n_scored }}</td>
      <td class="num strong">
        {{ '%.4f'|format(r.mean_chamfer) if r.mean_chamfer is not none else '—'
        }}
      </td>
      <td class="num">
        {{ '%.3f'|format(r.mean_fscore) if r.mean_fscore is not none else '—' }}
      </td>
      <td class="num">
        {{ '%.2f'|format(r.mean_structural) if r.mean_structural is not none
        else '—' }}
      </td>
      <td class="num">
        {{ '%.0f%%'|format(r.species_pass_rate * 100) if r.species_pass_rate is
        not none else '—' }}
      </td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% endif %} {% endfor %} {% endif %}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_difficulty_view.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Full suite + commit**

Run: `.venv/bin/pytest -q`
Expected: PASS — all green, including the existing `tests/test_difficulty_endpoint.py` / `test_difficulty_page.py` (the `/api/difficulty.json` change keeps `scorecard` and only adds `paradigm_grid`; the template only adds a section). If either existing test asserts something now shifted, reconcile it (do not weaken a real assertion without cause).

```bash
git add app/main.py app/templates/difficulty.html tests/test_difficulty_view.py
git commit -m "feat(difficulty): paradigm × tier grid on /difficulty + api endpoint"
```

---

## Post-implementation (controller, not a task)

After all tasks are green, run the seeder against a **study-DB copy** to populate real tiers and confirm the grid renders with live data:

```bash
cp data/study/arena-study.db /tmp/bio3d_test_difficulty.db
BIO3D_DATABASE_URL=sqlite:////tmp/bio3d_test_difficulty.db .venv/bin/python scripts/assign_difficulty.py
# expect: {'seeded': 7, 'materialized': 11, 'taxa': 7}
```

This is verification, not a code change — it confirms all 11 live tasks resolve to the 7 rubric taxa (empty `skipped`, so no raise) and tier. The `/tmp/` path passes `is_safe_test_db_target`; the real study DB is never written.
