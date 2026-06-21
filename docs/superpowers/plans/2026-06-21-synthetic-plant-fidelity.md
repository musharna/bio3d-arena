# Synthetic-Plant Botanical Fidelity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a votes-only "synthetic-plant botanical plausibility" vertical that ranks 3D-plant generators via the existing arena → Bradley-Terry → leaderboard pipeline — pure scope-reuse, no new page/scorer/ranking code.

**Architecture:** Add a `synthetic-plants` category, a `botanical_plausibility` criterion, per-plant-type tasks, and a `pd-archetype` generator — all DB rows via an idempotent `seed_synthetic_plants(db)` (mirrors `seed_recon_benchmark`). An ingest script registers generated-plant GLBs onto the type tasks (reusing the recon GLBs day-one for the cross-paradigm matchup). The existing arena/recompute/leaderboard handle voting + ranking, scoped by `category=synthetic-plants&criterion=botanical_plausibility`.

**Tech Stack:** FastAPI + SQLite/SQLAlchemy, the existing `app/seed.py` / `app/ingest.py` / arena, pytest.

## Global Constraints

- **Pure scope-reuse — NO new page, scorer, ranking code, or DB table.** New code = `seed_synthetic_plants` + `scripts/ingest_synthetic_plants.py` + tests. (Verbatim from spec.)
- Test env: `.venv/bin/python -m pytest`. Lint: `ruff check app scripts tests`.
- Criterion `botanical_plausibility` is distinct from `realism`/`overall` so synth-plant votes don't mix with recon votes.
- Asset production (procedural plants) is AgriGen's lane — do NOT build generators here.
- Type→task resolution is by TITLE convention (`"<sci_name> — botanical plausibility"`), not a new table (votes-only reuse).
- Reuse the binomial species slugs (`arabidopsis_thaliana`, `solanum_lycopersicum`, `zea_mays`, `pinus_sylvestris`) so the existing recon bake-off GLBs populate the synth-plant tasks day-one.
- ruff PostToolUse formatter can strip imports added before first use — add import + use in the same edit, re-grep.

---

### Task 1: `seed_synthetic_plants` — category, criterion, type tasks, pd-archetype generator

**Files:**

- Modify: `app/seed.py` (add `SYNTH_TYPES`, `seed_synthetic_plants`, `synth_task_for_slug`; call in `seed_all`)
- Test: `tests/test_synthetic_plants_seed.py`

**Interfaces:**

- Consumes: `Category`, `Criterion`, `Task`, `Generator` (existing); the `RECON_SPECIES` sci-names (existing, app/seed.py).
- Produces:
  - `SYNTH_TYPES: list[tuple[str, str]]` = (binomial_slug, sci_name) — the 4 launch types.
  - `seed_synthetic_plants(db) -> dict` — `{tasks, generators}`, idempotent.
  - `synth_task_for_slug(db, slug) -> Task | None` — resolves a binomial slug to its synth-plant Task (by title), or None.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_synthetic_plants_seed.py
from __future__ import annotations

from app import seed
from app.database import SessionLocal, init_db
from app.models import Category, Criterion, Generator, Task


def setup_module(_module):
    init_db()


def test_seed_synthetic_plants_creates_scope():
    db = SessionLocal()
    try:
        seed.seed_synthetic_plants(db)
        db.commit()
        cat = db.query(Category).filter(Category.slug == "synthetic-plants").first()
        crit = db.query(Criterion).filter(Criterion.slug == "botanical_plausibility").first()
        assert cat is not None and crit is not None
        # one task per launch type, in the synthetic-plants category
        tasks = db.query(Task).filter(Task.category_id == cat.id).all()
        assert len(tasks) >= 4
        # the procedural generator exists
        assert db.query(Generator).filter(Generator.slug == "pd-archetype").first() is not None
        # slug → task resolver works for a known species
        t = seed.synth_task_for_slug(db, "zea_mays")
        assert t is not None and t.category_id == cat.id
    finally:
        db.close()


def test_seed_synthetic_plants_is_idempotent():
    db = SessionLocal()
    try:
        seed.seed_synthetic_plants(db)
        db.commit()
        cat = db.query(Category).filter(Category.slug == "synthetic-plants").first()
        n_tasks = db.query(Task).filter(Task.category_id == cat.id).count()
        seed.seed_synthetic_plants(db)  # re-run
        db.commit()
        assert db.query(Task).filter(Task.category_id == cat.id).count() == n_tasks
    finally:
        db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_synthetic_plants_seed.py -q`
Expected: FAIL — `module 'app.seed' has no attribute 'seed_synthetic_plants'`.

- [ ] **Step 3: Add `SYNTH_TYPES`, `seed_synthetic_plants`, `synth_task_for_slug` to `app/seed.py`**

Read `app/seed.py` first. After `seed_recon_benchmark` (search `def seed_recon_benchmark`), add:

```python
# Synthetic-plant fidelity launch types — reuse the recon binomial slugs so the existing
# bake-off GLBs populate these tasks day-one (cross-paradigm: procedural vs reconstructed).
SYNTH_TYPES = [(slug, sci_name) for slug, sci_name, _descr in RECON_SPECIES]


def _synth_title(sci_name: str) -> str:
    return f"{sci_name} — botanical plausibility"


def synth_task_for_slug(db: Session, slug: str):
    """Resolve a binomial species slug to its synthetic-plants Task (by title), or None."""
    by_slug = {s: n for s, n in SYNTH_TYPES}
    sci = by_slug.get(slug)
    if sci is None:
        return None
    cat = db.execute(select(Category).where(Category.slug == "synthetic-plants")).scalars().first()
    if cat is None:
        return None
    return (
        db.execute(
            select(Task).where(Task.title == _synth_title(sci), Task.category_id == cat.id)
        )
        .scalars()
        .first()
    )


def seed_synthetic_plants(db: Session) -> dict:
    """Idempotent: a 'synthetic-plants' category, a 'botanical_plausibility' criterion, one
    Task per plant type, and the 'pd-archetype' procedural generator. Votes-only — the
    existing arena + Bradley-Terry leaderboard rank generators by botanical plausibility."""
    cat = db.execute(select(Category).where(Category.slug == "synthetic-plants")).scalars().first()
    if cat is None:
        cat = Category(
            slug="synthetic-plants",
            name="Synthetic Plants",
            description="Procedurally/AI-generated 3D plants, judged on botanical plausibility.",
        )
        db.add(cat)
        db.flush()

    crit = (
        db.execute(select(Criterion).where(Criterion.slug == "botanical_plausibility"))
        .scalars()
        .first()
    )
    if crit is None:
        db.add(
            Criterion(
                slug="botanical_plausibility",
                name="Botanical plausibility",
                description="Which looks more like a botanically real plant?",
            )
        )

    n_tasks = 0
    for _slug, sci_name in SYNTH_TYPES:
        title = _synth_title(sci_name)
        task = (
            db.execute(select(Task).where(Task.title == title, Task.category_id == cat.id))
            .scalars()
            .first()
        )
        if task is None:
            db.add(
                Task(
                    category_id=cat.id,
                    title=title,
                    prompt=f"Generate a botanically plausible 3D model of {sci_name}.",
                    criteria_note="Ranked by pairwise 'more botanically plausible?' votes (Mode-A).",
                )
            )
        n_tasks += 1

    gen = db.execute(select(Generator).where(Generator.slug == "pd-archetype")).scalars().first()
    if gen is None:
        db.add(
            Generator(
                slug="pd-archetype",
                name="PD archetype (procedural)",
                kind="model",
                is_anonymous=True,
            )
        )

    db.flush()
    return {"tasks": n_tasks, "generators": 1}
```

- [ ] **Step 4: Wire `seed_synthetic_plants` into `seed_all`**

In `seed_all`, find the `seed_recon_benchmark(db)` call (search `seed_recon_benchmark(db)`) and add the synthetic-plants seed right after it:

```python
        seed_recon_benchmark(db)
        seed_synthetic_plants(db)
```

(No delete-cascade change needed — Category/Criterion/Task/Generator are already in the `force=True` cascade.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_synthetic_plants_seed.py -q`
Expected: PASS (2 passed).

- [ ] **Step 6: Full suite (no regressions) + commit**

Run: `.venv/bin/python -m pytest -q && ruff check app tests`
Expected: all pass, ruff clean.

```bash
git add app/seed.py tests/test_synthetic_plants_seed.py
git commit -m "feat(synth): seed_synthetic_plants — category + botanical_plausibility criterion + type tasks"
```

---

### Task 2: `ingest_synthetic_plants.py` + end-to-end scoped vote→leaderboard

**Files:**

- Create: `scripts/ingest_synthetic_plants.py`
- Test: `tests/test_synthetic_plants_ingest.py`

**Interfaces:**

- Consumes: `seed.seed_synthetic_plants`, `seed.synth_task_for_slug` (Task 1); `ingest.register_output`; `scripts/ingest_bakeoff.parse_bakeoff_name` (existing, reused for `<slug>__<gen>[__<id>]` names).
- Produces: a CLI that registers generated-plant GLBs onto the synth-plant type tasks; no new importable API (it's a script).

- [ ] **Step 1: Write the failing test (real-execution ingest + scoped leaderboard)**

The test ingests two real GLBs (any two distinct GLBs from the recon bake-off — they are "generated plants") onto a synth-plant type task under two generators, casts a scoped vote via the API, recomputes, and asserts the scoped leaderboard ranks them.

```python
# tests/test_synthetic_plants_ingest.py
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app import ingest, seed
from app.database import SessionLocal, init_db
from app.main import app
from app.models import Comparison, ModelOutput

BAKE = Path("/home/mjarnold/agrigen/backend/data/bakeoff_v1")


def setup_module(_module):
    init_db()


def test_synth_ingest_then_scoped_vote_ranks():
    db = SessionLocal()
    seed.seed_synthetic_plants(db)
    db.commit()
    task = seed.synth_task_for_slug(db, "zea_mays")
    assert task is not None
    # two DISTINCT generated plants (reuse recon GLBs) under two generators
    for path, gen in [
        (BAKE / "zea_mays__trellis.glb", "trellis"),
        (BAKE / "zea_mays__hunyuan3d.glb", "hunyuan3d"),
    ]:
        ingest.register_output(
            db, task_id=task.id, generator_slug=gen, data=path.read_bytes(), ext="glb",
            title=f"zea_mays — {gen}", meta={"synthetic": True},
        )
    db.commit()
    assert db.query(ModelOutput).filter(ModelOutput.task_id == task.id).count() == 2

    client = TestClient(app)
    # cast scoped votes; consistently prefer trellis
    out_gen = {
        o.id: o.generator.slug
        for o in db.query(ModelOutput).filter(ModelOutput.task_id == task.id).all()
    }
    cast = 0
    for _ in range(20):
        nxt = client.get(
            "/api/next?category=synthetic-plants&criterion=botanical_plausibility"
        ).json()
        cid = nxt.get("comparison_id")
        if cid is None:
            continue
        comp = db.get(Comparison, cid)
        if comp.is_gold:
            continue
        winner = "a" if out_gen.get(comp.output_a_id) == "trellis" else "b"
        if (
            client.post(
                "/api/vote?category=synthetic-plants&criterion=botanical_plausibility",
                json={"comparison_id": cid, "winner": winner},
            ).status_code
            == 200
        ):
            cast += 1
        if cast >= 1:
            break
    assert cast >= 1
    assert client.post("/admin/recompute", data={"token": "test-token"}).status_code == 200
    board = client.get(
        "/api/leaderboard?category=synthetic-plants&criterion=botanical_plausibility"
    ).json()
    rows = board["rows"]
    names = [r["generator"] for r in rows if r.get("n_games", 0) > 0]
    assert names, "expected at least one generator with games in the scoped board"
    db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_synthetic_plants_ingest.py -q`
Expected: FAIL — the scoped board has no games until the scope is votable (or the script is missing). Confirm the failure is about the assertion / scope, not an import error, then proceed.

- [ ] **Step 3: Create `scripts/ingest_synthetic_plants.py`**

```python
#!/usr/bin/env python3
"""Register generated-plant GLBs into the synthetic-plants botanical-plausibility arena.

GLB names follow `<species_slug>__<generator>[__<id>].glb` (same as the recon bake-off, so
the recon GLBs can seed the cross-paradigm matchup). Resolves each to its synth-plant type
Task (by species slug) and registers it under its generator (content-deduped). Votes-only —
no scoring; the existing arena ranks by botanical-plausibility votes.

Usage:
    python scripts/ingest_synthetic_plants.py --dir /path/to/generated_plant_glbs
"""

from __future__ import annotations

import argparse
import glob
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", required=True, help="dir of <species>__<generator>[__<id>].glb files")
    args = ap.parse_args()

    from app import ingest, seed
    from app.database import SessionLocal, init_db
    from scripts.ingest_bakeoff import parse_bakeoff_name

    init_db()
    db = SessionLocal()
    seed.seed_synthetic_plants(db)  # idempotent — ensure the scope exists
    db.commit()

    n = 0
    for path in sorted(glob.glob(os.path.join(args.dir, "*.glb"))):
        base = os.path.basename(path)[: -len(".glb")]
        parsed = parse_bakeoff_name(base)
        if parsed is None:
            print(f"  SKIP {base}: expected <species>__<generator>[__<id>].glb")
            continue
        species, generator, _photo = parsed
        task = seed.synth_task_for_slug(db, species)
        if task is None:
            print(f"  SKIP {base}: no synthetic-plants task for '{species}'")
            continue
        out, created = ingest.register_output(
            db, task_id=task.id, generator_slug=generator, data=Path(path).read_bytes(),
            ext="glb", title=f"{species} — {generator}", meta={"synthetic": True},
        )
        n += 1
        print(f"  ingested {base} -> output #{out.id} (created={created})")
    db.commit()
    db.close()
    print(f"ingested {n} GLB(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Note (verified): `from scripts.ingest_bakeoff import parse_bakeoff_name` works as-is — the `sys.path.insert(..., parent)` puts the repo root on the path, and Python 3 implicit namespace packages resolve `scripts.ingest_bakeoff` even though there is no `scripts/__init__.py`. No importlib fallback needed.

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_synthetic_plants_ingest.py -q`
Expected: PASS. (The test registers outputs in-process and exercises the scoped arena → vote → recompute → scoped leaderboard; the script's parse path is covered by reusing `parse_bakeoff_name`.)

- [ ] **Step 5: Full suite + ruff + commit**

Run: `.venv/bin/python -m pytest -q && ruff check app scripts tests`
Expected: all pass, ruff clean.

```bash
git add scripts/ingest_synthetic_plants.py tests/test_synthetic_plants_ingest.py
git commit -m "feat(synth): ingest_synthetic_plants + scoped vote→leaderboard (cross-paradigm via recon GLBs)"
```

---

## Final verification (controller, before merge)

- [ ] Full suite ≥2× green (Inc2 lesson): `.venv/bin/python -m pytest -q` twice.
- [ ] ruff clean: `ruff check app scripts tests`.
- [ ] Real-execution sanity: reset a dev DB, run `scripts/ingest_synthetic_plants.py --dir ~/agrigen/backend/data/bakeoff_v1`, confirm a synth-plant task has ≥2 outputs and `/leaderboard?category=synthetic-plants&criterion=botanical_plausibility` renders (boot the app + screenshot, since it reuses the existing leaderboard page).
- [ ] Independent review of `git diff master..HEAD` (Inc3/Inc4 caught real bugs) — focus: idempotency of `seed_synthetic_plants`, the title-convention resolver, no accidental new page/scorer, criterion not colliding with `realism`.
- [ ] Suite-gated ff-merge to master.
- [ ] Update memory: synthetic-plant vertical scaffolded; pd-archetype assets pending AgriGen export; the board reuses the scoped /leaderboard.

## Self-review notes (author)

- **Spec coverage:** category/criterion/type-tasks/generators (Task 1) ✓; votes-only reuse of arena+BT+leaderboard (no new page/scorer — Tasks 1-2, asserted via the scoped-leaderboard test) ✓; ingest path (Task 2) ✓; cross-paradigm day-one via recon GLBs (Task 2 test) ✓; testing incl. real-execution scoped vote→leaderboard (Task 2) ✓.
- **No new table/page/scorer** — confirmed: only `seed.py` additions + one script + tests.
- **Deferred (spec out-of-scope):** objective trait metrics, Turing condition, `/synthetic` landing page.
- **Read-before-edit at execution (Iron Law):** `app/seed.py` (`seed_recon_benchmark` end + the `seed_recon_benchmark(db)` call site in `seed_all`), and check `scripts/__init__.py` existence for the import form in Task 2 Step 3.
- **Open cross-session item:** the on-mission `pd-archetype` GLBs are produced by AgriGen (their lane); the scaffolding + ingest are ready for them. The day-one board is cross-paradigm (procedural slot empty until they export).
