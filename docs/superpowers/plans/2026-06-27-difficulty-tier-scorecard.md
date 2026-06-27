# Difficulty-Tier Scorecard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a difficulty-tier dimension so objective recon accuracy can be sliced by task difficulty, via a per-tier scorecard over the existing `Metric`/`OrganMetric` data.

**Architecture:** A `TaskDifficulty` side table (create_all-only) tags each task with an ordinal tier + rationale. A pure aggregation `tier_scorecard(db)` joins the existing metric tables to the tier, grouped by (tier × generator). Two thin read-only consumers expose it: a JSON endpoint and a dated markdown report.

**Tech Stack:** FastAPI + SQLAlchemy (SQLite), pytest. No new dependencies.

## Global Constraints

- Schema is **create_all-only — NO ALTER/migration**. New behavior is a NEW table, never a column on an existing table.
- **Human voting/ranking path untouched** — no edits to `Vote`/`Rating`/`apply_vote`/`api_vote`/`_matches_for_scope`.
- **Honest N/A, never silent drops** — untiered tasks → an `"untiered"` bucket; unscored outputs → excluded from means, rendered `—`.
- Tier vocabulary: `TIERS = ("easy", "moderate", "hard")` (canonical order). Validate membership; raise `ValueError` on anything else.
- TDD, frequent commits. Tests use `init_db()` in `setup_module` and unique row prefixes (shared persistent test DB).
- Means skip `None`; a mean over zero values is `None`.

---

### Task 1: `TaskDifficulty` table + tier vocabulary

**Files:**

- Modify: `app/models.py` (append a new model class, end of file)
- Create: `app/difficulty.py`
- Test: `tests/test_difficulty_schema.py`

**Interfaces:**

- Produces: `TaskDifficulty` ORM model (`task_difficulty` table; columns `id`, `task_id` unique FK, `tier`, `rationale`, `updated`); `app.difficulty.TIERS: tuple[str,str,str]`; `app.difficulty.TIER_ORDER: dict[str,int]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_difficulty_schema.py
from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from app.database import SessionLocal, init_db
from app.difficulty import TIER_ORDER, TIERS
from app.models import Category, Task, TaskDifficulty


def setup_module(_m):
    init_db()


def test_tiers_vocab_ordered():
    assert TIERS == ("easy", "moderate", "hard")
    assert [TIER_ORDER[t] for t in TIERS] == [0, 1, 2]


def _task(db):
    cat = Category(slug="td1-cat", name="C")
    db.add(cat)
    db.flush()
    task = Task(category_id=cat.id, title="td1-task", prompt="p")
    db.add(task)
    db.flush()
    return task


def test_task_difficulty_roundtrip_and_unique():
    with SessionLocal() as db:
        db.query(TaskDifficulty).delete()
        db.query(Task).filter(Task.title == "td1-task").delete(synchronize_session=False)
        db.query(Category).filter_by(slug="td1-cat").delete(synchronize_session=False)
        db.commit()
        task = _task(db)
        db.add(TaskDifficulty(task_id=task.id, tier="hard", rationale="thin structure"))
        db.commit()
        row = db.query(TaskDifficulty).filter_by(task_id=task.id).first()
        assert row.tier == "hard"
        assert row.rationale == "thin structure"

        # task_id is unique — a second row for the same task must fail.
        db.add(TaskDifficulty(task_id=task.id, tier="easy", rationale="dup"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_difficulty_schema.py -q`
Expected: FAIL — `ImportError: cannot import name 'TaskDifficulty'` / `app.difficulty`.

- [ ] **Step 3: Write minimal implementation**

Append to `app/models.py` (after the last model class):

```python
class TaskDifficulty(Base):
    """Manually-curated difficulty tier for a benchmark Task. Separate table (not a
    Task column) to honor the create_all-only schema — mirrors ReconTask/OrganMetric.
    tier ∈ difficulty.TIERS; rationale is free text (why this tier)."""

    __tablename__ = "task_difficulty"
    __table_args__ = (UniqueConstraint("task_id", name="uq_task_difficulty_task"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("task.id"), unique=True, index=True)
    tier: Mapped[str] = mapped_column(String(16))
    rationale: Mapped[str] = mapped_column(Text, default="")
    updated: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)
```

Create `app/difficulty.py`:

```python
"""Difficulty-tier dimension: vocabulary, assignment, and the objective scorecard.

Tiers are a manually-curated property of a benchmark Task (TaskDifficulty side table).
The scorecard aggregates the EXISTING objective metrics (Metric, OrganMetric) by
(tier × generator) — it never recomputes Bradley-Terry and never touches the human path.
"""

from __future__ import annotations

TIERS: tuple[str, str, str] = ("easy", "moderate", "hard")
TIER_ORDER: dict[str, int] = {t: i for i, t in enumerate(TIERS)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_difficulty_schema.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add app/models.py app/difficulty.py tests/test_difficulty_schema.py
git commit -m "feat(difficulty): TaskDifficulty table + tier vocabulary"
```

---

### Task 2: `set_task_difficulty` assignment helper

**Files:**

- Modify: `app/difficulty.py`
- Test: `tests/test_difficulty_assign.py`

**Interfaces:**

- Consumes: `TaskDifficulty`, `TIERS` (Task 1).
- Produces: `set_task_difficulty(db, task_id: int, tier: str, rationale: str = "", commit: bool = True) -> TaskDifficulty` — validates `tier in TIERS` and that the task exists (both raise `ValueError`); upserts by `task_id`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_difficulty_assign.py
from __future__ import annotations

import pytest

from app.database import SessionLocal, init_db
from app.difficulty import set_task_difficulty
from app.models import Category, Task, TaskDifficulty


def setup_module(_m):
    init_db()


def _task(db):
    cat = Category(slug="td2-cat", name="C")
    db.add(cat)
    db.flush()
    task = Task(category_id=cat.id, title="td2-task", prompt="p")
    db.add(task)
    db.flush()
    return task


def _clean(db):
    db.query(TaskDifficulty).delete()
    db.query(Task).filter(Task.title == "td2-task").delete(synchronize_session=False)
    db.query(Category).filter_by(slug="td2-cat").delete(synchronize_session=False)
    db.commit()


def test_set_valid_then_upsert():
    with SessionLocal() as db:
        _clean(db)
        task = _task(db)
        db.commit()
        row = set_task_difficulty(db, task.id, "easy", "open canopy")
        assert row.tier == "easy"
        # Re-assign updates in place (no duplicate row).
        row2 = set_task_difficulty(db, task.id, "hard", "occlusion")
        assert row2.tier == "hard"
        assert db.query(TaskDifficulty).filter_by(task_id=task.id).count() == 1


def test_invalid_tier_raises():
    with SessionLocal() as db:
        _clean(db)
        task = _task(db)
        db.commit()
        with pytest.raises(ValueError):
            set_task_difficulty(db, task.id, "extreme", "")


def test_unknown_task_raises():
    with SessionLocal() as db:
        _clean(db)
        with pytest.raises(ValueError):
            set_task_difficulty(db, 999999, "easy", "")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_difficulty_assign.py -q`
Expected: FAIL — `ImportError: cannot import name 'set_task_difficulty'`.

- [ ] **Step 3: Write minimal implementation**

Append to `app/difficulty.py`:

```python
from sqlalchemy import select  # noqa: E402  (grouped at use to keep the module's intro clean)

from .models import Task, TaskDifficulty  # noqa: E402


def set_task_difficulty(
    db, task_id: int, tier: str, rationale: str = "", commit: bool = True
) -> TaskDifficulty:
    """Assign (or re-assign) a task's difficulty tier. Upserts by task_id."""
    if tier not in TIERS:
        raise ValueError(f"tier must be one of {TIERS}, got {tier!r}")
    if db.get(Task, task_id) is None:
        raise ValueError(f"no task with id {task_id}")
    row = db.execute(
        select(TaskDifficulty).where(TaskDifficulty.task_id == task_id)
    ).scalars().first()
    if row is None:
        row = TaskDifficulty(task_id=task_id, tier=tier, rationale=rationale)
        db.add(row)
    else:
        row.tier = tier
        row.rationale = rationale
    if commit:
        db.commit()
    return row
```

(Move the two `import` lines to the top of `app/difficulty.py` with the other imports; they are shown here next to their use for clarity. The module top should read: stdlib/`__future__`, then `from sqlalchemy import select`, then `from .models import Task, TaskDifficulty`, then the `TIERS`/`TIER_ORDER` constants.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_difficulty_assign.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add app/difficulty.py tests/test_difficulty_assign.py
git commit -m "feat(difficulty): set_task_difficulty assignment helper"
```

---

### Task 3: `tier_scorecard` aggregation

**Files:**

- Modify: `app/difficulty.py`
- Test: `tests/test_difficulty_scorecard.py`

**Interfaces:**

- Consumes: `TaskDifficulty`, `TIERS`, `TIER_ORDER` (Task 1); existing `Metric`, `OrganMetric`, `ModelOutput`, `Generator`.
- Produces: `tier_scorecard(db) -> list[dict]` — one dict per tier in `TIERS` then `"untiered"`, shape:
  `{"tier": str, "rows": [{"generator": str, "n_outputs": int, "n_scored": int, "mean_chamfer": float|None, "mean_fscore": float|None, "mean_structural": float|None, "species_pass_rate": float|None}]}`. Rows sorted by generator name. `is_gold` outputs excluded.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_difficulty_scorecard.py
from __future__ import annotations

from app.database import SessionLocal, init_db
from app.difficulty import set_task_difficulty, tier_scorecard
from app.models import (
    Category,
    Generator,
    Metric,
    ModelOutput,
    OrganMetric,
    Task,
    TaskDifficulty,
)


def setup_module(_m):
    init_db()


def _clean(db):
    db.query(TaskDifficulty).delete()
    db.query(Metric).filter(Metric.detail == "td3").delete(synchronize_session=False)
    db.query(OrganMetric).filter(OrganMetric.detail == "td3").delete(synchronize_session=False)
    db.query(ModelOutput).filter(ModelOutput.asset_path.like("td3/%.glb")).delete(
        synchronize_session=False
    )
    db.query(Task).filter(Task.title.like("td3-%")).delete(synchronize_session=False)
    db.query(Generator).filter(Generator.slug.like("td3-%")).delete(synchronize_session=False)
    db.query(Category).filter_by(slug="td3-cat").delete(synchronize_session=False)
    db.commit()


def test_scorecard_groups_by_tier_and_generator():
    with SessionLocal() as db:
        _clean(db)
        cat = Category(slug="td3-cat", name="C")
        db.add(cat)
        gen = Generator(slug="td3-g", name="Gen")
        db.add_all([cat, gen])
        db.flush()
        hard = Task(category_id=cat.id, title="td3-hard", prompt="p")
        untiered = Task(category_id=cat.id, title="td3-unt", prompt="p")
        db.add_all([hard, untiered])
        db.flush()
        # Two scored outputs in the HARD task; one in the UNTIERED task.
        o1 = ModelOutput(task_id=hard.id, generator_id=gen.id, asset_path="td3/1.glb")
        o2 = ModelOutput(task_id=hard.id, generator_id=gen.id, asset_path="td3/2.glb")
        o3 = ModelOutput(task_id=untiered.id, generator_id=gen.id, asset_path="td3/3.glb")
        db.add_all([o1, o2, o3])
        db.flush()
        db.add_all([
            Metric(output_id=o1.id, chamfer=0.2, fscore=0.6, species_verdict="PASS", detail="td3"),
            Metric(output_id=o2.id, chamfer=0.4, fscore=0.8, species_verdict="FAIL", detail="td3"),
            # o3 has no Metric (unscored) → counts toward n_outputs, not n_scored.
            OrganMetric(output_id=o1.id, botanical_fidelity=0.5, detail="td3"),
        ])
        set_task_difficulty(db, hard.id, "hard", "occlusion", commit=False)
        db.commit()

        card = tier_scorecard(db)
        by_tier = {c["tier"]: c for c in card}
        assert [c["tier"] for c in card] == ["easy", "moderate", "hard", "untiered"]

        hard_rows = [r for r in by_tier["hard"]["rows"] if r["generator"] == "Gen"]
        assert len(hard_rows) == 1
        r = hard_rows[0]
        assert r["n_outputs"] == 2 and r["n_scored"] == 2
        assert abs(r["mean_chamfer"] - 0.3) < 1e-9
        assert abs(r["mean_fscore"] - 0.7) < 1e-9
        assert abs(r["mean_structural"] - 0.5) < 1e-9  # only o1 has an OrganMetric
        assert abs(r["species_pass_rate"] - 0.5) < 1e-9  # 1 PASS of 2

        unt_rows = [r for r in by_tier["untiered"]["rows"] if r["generator"] == "Gen"]
        assert len(unt_rows) == 1
        assert unt_rows[0]["n_outputs"] == 1 and unt_rows[0]["n_scored"] == 0
        assert unt_rows[0]["mean_chamfer"] is None  # unscored → None, not 0
        assert by_tier["easy"]["rows"] == []  # empty tier present, honest
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_difficulty_scorecard.py -q`
Expected: FAIL — `ImportError: cannot import name 'tier_scorecard'`.

- [ ] **Step 3: Write minimal implementation**

Append to `app/difficulty.py` (and extend the `from .models import …` line to include `Generator, Metric, ModelOutput, OrganMetric`):

```python
def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def tier_scorecard(db) -> list[dict]:
    """Per-(tier × generator) aggregate of the existing objective metrics.

    Tiers in canonical order, then an 'untiered' bucket for tasks with no
    TaskDifficulty row. Means skip missing metric rows (None), never zero-fill.
    """
    from sqlalchemy import select

    from .models import Generator, Metric, ModelOutput, OrganMetric

    tier_by_task = {
        td.task_id: td.tier for td in db.execute(select(TaskDifficulty)).scalars()
    }
    gen_name = {g.id: g.name for g in db.execute(select(Generator)).scalars()}
    chamfer_by_out = {}
    fscore_by_out = {}
    verdict_by_out = {}
    for m in db.execute(select(Metric)).scalars():
        chamfer_by_out[m.output_id] = m.chamfer
        fscore_by_out[m.output_id] = m.fscore
        verdict_by_out[m.output_id] = m.species_verdict
    structural_by_out = {
        om.output_id: om.botanical_fidelity for om in db.execute(select(OrganMetric)).scalars()
    }

    # acc[(tier, gen_id)] = dict of running lists/counters
    acc: dict[tuple[str, int], dict] = {}
    for out in db.execute(select(ModelOutput).where(ModelOutput.is_gold.is_(False))).scalars():
        tier = tier_by_task.get(out.task_id, "untiered")
        key = (tier, out.generator_id)
        a = acc.setdefault(
            key,
            {"n_outputs": 0, "n_scored": 0, "chamfer": [], "fscore": [], "structural": [],
             "verdicts": []},
        )
        a["n_outputs"] += 1
        scored = out.id in chamfer_by_out
        if scored:
            a["n_scored"] += 1
            if chamfer_by_out[out.id] is not None:
                a["chamfer"].append(chamfer_by_out[out.id])
            if fscore_by_out[out.id] is not None:
                a["fscore"].append(fscore_by_out[out.id])
            if verdict_by_out[out.id] is not None:
                a["verdicts"].append(verdict_by_out[out.id])
        if structural_by_out.get(out.id) is not None:
            a["structural"].append(structural_by_out[out.id])

    out_tiers = list(TIERS) + ["untiered"]
    card = []
    for tier in out_tiers:
        rows = []
        for (t, gid), a in acc.items():
            if t != tier:
                continue
            verdicts = a["verdicts"]
            pass_rate = (
                sum(1 for v in verdicts if v == "PASS") / len(verdicts) if verdicts else None
            )
            rows.append(
                {
                    "generator": gen_name.get(gid, f"#{gid}"),
                    "n_outputs": a["n_outputs"],
                    "n_scored": a["n_scored"],
                    "mean_chamfer": _mean(a["chamfer"]),
                    "mean_fscore": _mean(a["fscore"]),
                    "mean_structural": _mean(a["structural"]),
                    "species_pass_rate": pass_rate,
                }
            )
        rows.sort(key=lambda r: r["generator"])
        card.append({"tier": tier, "rows": rows})
    return card
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_difficulty_scorecard.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/difficulty.py tests/test_difficulty_scorecard.py
git commit -m "feat(difficulty): tier_scorecard aggregation over existing metrics"
```

---

### Task 4: `GET /api/difficulty.json` endpoint

**Files:**

- Modify: `app/main.py` (add endpoint near the other `/api/*.json` routes; import `difficulty`)
- Test: `tests/test_difficulty_endpoint.py`

**Interfaces:**

- Consumes: `app.difficulty.tier_scorecard` (Task 3).
- Produces: `GET /api/difficulty.json` → `{"scorecard": tier_scorecard(db)}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_difficulty_endpoint.py
from __future__ import annotations

from fastapi.testclient import TestClient

from app.database import init_db
from app.main import app


def setup_module(_m):
    init_db()


def test_difficulty_endpoint_returns_scorecard_shape():
    client = TestClient(app)
    resp = client.get("/api/difficulty.json")
    assert resp.status_code == 200
    body = resp.json()
    assert "scorecard" in body
    tiers = [c["tier"] for c in body["scorecard"]]
    assert tiers == ["easy", "moderate", "hard", "untiered"]
    for c in body["scorecard"]:
        assert isinstance(c["rows"], list)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_difficulty_endpoint.py -q`
Expected: FAIL — 404 (route not defined).

- [ ] **Step 3: Write minimal implementation**

In `app/main.py`, add `difficulty` to the `from . import …` imports if such a group exists, otherwise add `from . import difficulty` near the top with the other `app` imports. Then add the route alongside the other `/api/*.json` endpoints (e.g. after `export_dataset`):

```python
@app.get("/api/difficulty.json")
def api_difficulty(db: Session = Depends(get_db)):
    """Per-(difficulty-tier × generator) objective scorecard over existing metrics."""
    return {"scorecard": difficulty.tier_scorecard(db)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_difficulty_endpoint.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/main.py tests/test_difficulty_endpoint.py
git commit -m "feat(difficulty): /api/difficulty.json scorecard endpoint"
```

---

### Task 5: `difficulty_report.py` dated markdown report

**Files:**

- Create: `scripts/difficulty_report.py`
- Test: `tests/test_difficulty_report.py`

**Interfaces:**

- Consumes: `app.difficulty.tier_scorecard` (Task 3).
- Produces: `build_report(db) -> str`; `main()` writing `docs/results/<date>-difficulty-scorecard.md` via `--date`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_difficulty_report.py
from __future__ import annotations

from app.database import SessionLocal, init_db

from scripts.difficulty_report import build_report


def setup_module(_m):
    init_db()


def test_report_has_a_section_per_tier_with_header_and_empty_placeholder():
    with SessionLocal() as db:
        text = build_report(db)
    for tier in ("easy", "moderate", "hard", "untiered"):
        assert f"## Tier: {tier}" in text
    assert "| generator |" in text  # header row present
    # A tier with no rows renders the honest placeholder (em-dash only appears once a
    # tier has scored-but-partial rows, which this empty-DB report does not seed).
    assert "_(no tasks in this tier)_" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_difficulty_report.py -q`
Expected: FAIL — `ModuleNotFoundError: scripts.difficulty_report`.

- [ ] **Step 3: Write minimal implementation**

Create `scripts/difficulty_report.py`:

```python
"""Generate the difficulty-tier scorecard → docs/results/<date>-difficulty-scorecard.md.

Usage: .venv/bin/python scripts/difficulty_report.py --date 2026-06-27"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import difficulty  # noqa: E402
from app.database import SessionLocal  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def _fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:.3f}"


def build_report(db) -> str:
    lines = ["# Difficulty-Tier Scorecard", ""]
    for card in difficulty.tier_scorecard(db):
        lines.append(f"## Tier: {card['tier']}")
        lines.append("")
        lines.append(
            "| generator | n | scored | mean chamfer↓ | mean F-score↑ | "
            "mean structural↑ | species PASS-rate↑ |"
        )
        lines.append("|---|---|---|---|---|---|---|")
        if not card["rows"]:
            lines.append("| _(no tasks in this tier)_ | | | | | | |")
        for r in card["rows"]:
            lines.append(
                f"| {r['generator']} | {r['n_outputs']} | {r['n_scored']} | "
                f"{_fmt(r['mean_chamfer'])} | {_fmt(r['mean_fscore'])} | "
                f"{_fmt(r['mean_structural'])} | {_fmt(r['species_pass_rate'])} |"
            )
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", required=True, help="YYYY-MM-DD for the output filename")
    args = ap.parse_args()
    with SessionLocal() as db:
        text = build_report(db)
    out = ROOT / "docs" / "results" / f"{args.date}-difficulty-scorecard.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_difficulty_report.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/difficulty_report.py tests/test_difficulty_report.py
git commit -m "feat(difficulty): dated markdown scorecard report"
```

---

### Task 6: `assign_difficulty.py` bulk-assignment CLI + initial curation

**Files:**

- Create: `scripts/assign_difficulty.py`
- Test: `tests/test_assign_difficulty.py`

**Interfaces:**

- Consumes: `app.difficulty.set_task_difficulty` (Task 2); `ReconTask` (existing: `task_id`, `species_slug`).
- Produces: `DIFFICULTY_MAP: dict[str, tuple[str, str]]` (species_slug → (tier, rationale)); `assign_all(db, mapping=DIFFICULTY_MAP) -> dict` returning `{"assigned": int, "skipped": list[str]}` (skipped = slugs with no ReconTask). Idempotent (upserts).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_assign_difficulty.py
from __future__ import annotations

from app.database import SessionLocal, init_db
from app.models import Category, ReconTask, Task, TaskDifficulty

from scripts.assign_difficulty import assign_all


def setup_module(_m):
    init_db()


def test_assign_all_maps_slugs_and_skips_missing():
    with SessionLocal() as db:
        db.query(TaskDifficulty).delete()
        db.query(ReconTask).filter(ReconTask.species_slug == "ad-sp").delete(
            synchronize_session=False
        )
        db.query(Task).filter(Task.title == "ad-task").delete(synchronize_session=False)
        db.query(Category).filter_by(slug="ad-cat").delete(synchronize_session=False)
        db.commit()
        cat = Category(slug="ad-cat", name="C")
        db.add(cat)
        db.flush()
        task = Task(category_id=cat.id, title="ad-task", prompt="p")
        db.add(task)
        db.flush()
        db.add(ReconTask(task_id=task.id, species_slug="ad-sp", species_name="Sp"))
        db.commit()

        res = assign_all(
            db,
            mapping={"ad-sp": ("hard", "test"), "ad-missing": ("easy", "nope")},
        )
        assert res["assigned"] == 1
        assert res["skipped"] == ["ad-missing"]
        row = db.query(TaskDifficulty).filter_by(task_id=task.id).first()
        assert row.tier == "hard"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_assign_difficulty.py -q`
Expected: FAIL — `ModuleNotFoundError: scripts.assign_difficulty`.

- [ ] **Step 3: Write minimal implementation**

Create `scripts/assign_difficulty.py`. The `DIFFICULTY_MAP` below is an EDITABLE starting
curation keyed by `ReconTask.species_slug`; the script skips-and-logs any slug with no
ReconTask, so a slug that doesn't match the live DB is non-fatal. Before relying on the
real assignment, run `select(ReconTask.species_slug)` against the target DB and adjust keys
to match the actual slugs.

```python
"""Bulk-assign difficulty tiers to recon tasks (idempotent upsert).

DIFFICULTY_MAP is keyed by ReconTask.species_slug → (tier, rationale). Slugs with no
ReconTask are skipped and logged (non-fatal). Verify slugs against the live DB:
    select(ReconTask.species_slug)

Usage: .venv/bin/python scripts/assign_difficulty.py"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.difficulty import set_task_difficulty  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import ReconTask  # noqa: E402

# Editable starting curation. Tiers reflect reconstruction difficulty of the subject.
DIFFICULTY_MAP: dict[str, tuple[str, str]] = {
    "tomato": ("easy", "compact bushy form, large leaves/fruit — forgiving geometry"),
    "soybean": ("moderate", "trifoliate leaves, moderate self-occlusion"),
    "rose": ("moderate", "layered petals; bloom interior is the hard sub-region"),
    "maize": ("moderate", "tall blade leaves, thin tassel/silk detail"),
    "arabidopsis": ("hard", "fine rosette + bolting inflorescence, thin stems"),
    "pine": ("hard", "dense fine needles + branch self-occlusion"),
}


def assign_all(db, mapping: dict[str, tuple[str, str]] = DIFFICULTY_MAP) -> dict:
    slug_to_task = {
        rt.species_slug: rt.task_id for rt in db.execute(select(ReconTask)).scalars()
    }
    assigned = 0
    skipped: list[str] = []
    for slug, (tier, rationale) in mapping.items():
        task_id = slug_to_task.get(slug)
        if task_id is None:
            skipped.append(slug)
            continue
        set_task_difficulty(db, task_id, tier, rationale, commit=False)
        assigned += 1
    db.commit()
    return {"assigned": assigned, "skipped": skipped}


def main() -> int:
    with SessionLocal() as db:
        res = assign_all(db)
    print(res)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_assign_difficulty.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/assign_difficulty.py tests/test_assign_difficulty.py
git commit -m "feat(difficulty): bulk-assign CLI + initial tier curation"
```

---

### Task 7: Full regression + integration sanity

**Files:** none (verification only)

- [ ] **Step 1: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS — prior count (308 passed / 8 skipped) plus the new difficulty tests, 0 failed.

- [ ] **Step 2: Smoke the endpoint + report against the study DB (no writes to originals)**

```bash
source "$CLAUDE_JOB_DIR/tmp/study_env.sh" 2>/dev/null || \
  export BIO3D_DATA_DIR=/home/mjarnold/bio3d-arena/.claude/worktrees/bio3d-arena-mvp/data \
         BIO3D_DB_PATH=/home/mjarnold/bio3d-arena/data/study/arena-study.db
.venv/bin/python scripts/assign_difficulty.py        # assign tiers to live recon tasks
.venv/bin/python scripts/difficulty_report.py --date 2026-06-27
```

Expected: `assign_difficulty` prints `{assigned: N, skipped: [...]}`; report writes
`docs/results/2026-06-27-difficulty-scorecard.md` with a section per tier.

- [ ] **Step 3: Confirm the human path is untouched**

Run: `git diff --stat main...HEAD -- app/service.py app/main.py`
Expected: `app/main.py` shows ONLY the added `/api/difficulty.json` route + the `difficulty`
import; `app/service.py` unchanged. No edits to `api_vote`/`apply_vote`/`_matches_for_scope`.

---

## Self-Review

**Spec coverage:** TaskDifficulty schema (T1) ✓; tier vocab (T1) ✓; set_task_difficulty (T2) ✓; tier_scorecard aggregation incl. untiered + unscored + species_pass_rate (T3) ✓; JSON endpoint (T4) ✓; markdown report (T5) ✓; assign CLI + curation (T6) ✓; honest N/A (T3 None handling, T5 em-dash, empty-tier placeholder) ✓; human path untouched (T7 §3) ✓; create_all-only new table (T1) ✓. No spec requirement left without a task.

**Placeholder scan:** No TBD/TODO; every code step shows complete code. The `DIFFICULTY_MAP` values are concrete editable defaults (skip-and-log makes a slug mismatch non-fatal), not placeholders.

**Type consistency:** `tier_scorecard` returns `list[{tier, rows:[…]}]` — consumed identically by T4 (`{"scorecard": …}`) and T5 (`card['tier']`, `card['rows']`, row keys `generator/n_outputs/n_scored/mean_chamfer/mean_fscore/mean_structural/species_pass_rate`). `set_task_difficulty(db, task_id, tier, rationale, commit)` signature matches its use in T6. `TIERS`/`TIER_ORDER` names consistent across tasks.
