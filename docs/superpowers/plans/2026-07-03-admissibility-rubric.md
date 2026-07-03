# Admissibility rubric Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Separate admissibility (binary, per-output "valid candidate for voting") from preference by routing the vote-pool gate through a pluggable predicate composer, with a domain-agnostic structural-validity predicate as the first member and the existing completeness gate folded in as the second.

**Architecture:** A `Predicate` protocol + a named rubric + one composer `admissibility.non_admitted_output_ids(db, rubric)` that the pool gate calls. The `structural` predicate is pure trimesh geometry, precomputed and stored per output in a new `Admissibility` table (mirroring D-Complete's enumerate/evaluate/upsert). The `completeness` predicate reuses the existing completeness-category exclusion. Conservative/precision-first: structural rejects only unambiguous degeneracy.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 (`Mapped`/`mapped_column`), trimesh 4.12 + numpy (already deps), pytest.

## Global Constraints

- Test runner `PYTHONPATH="$(pwd)" .venv/bin/pytest` with `BIO3D_DATABASE_URL` UNSET. Baseline: **660 passed, 8 skipped**.
- **NEVER** `BIO3D_DATABASE_URL=study` — the suite drops tables.
- Structural rejects ONLY: `unreadable | empty | non_finite | too_small | degenerate_bbox`. Conservative thresholds as module constants. **Zero false positives on good outputs is a merge-blocking acceptance gate** (validated in a real-execution run, below).
- Structural is precomputed/stored — NEVER loads GLBs per `/api/next` request.
- Reuse: `trimesh.load(path, force="mesh")` (repo idiom, ingest.py:61-74), the completeness `upsert`/`enumerate`/`score_outputs` fail-loud-per-output pattern (`app/completeness.py:101-175`), `flags.excluded_output_ids_by_completeness` (completeness predicate body), the `exclude_fn` pick_task/pick_pair PARITY, `_utcnow`.
- New `Admissibility` table via `create_all`; it is a ModelOutput child → MUST be added to `_FORCE_DELETE_MODELS` (the `tests/test_seed_force_cascade.py` drift-guard enforces it).
- Composer default rubric: `DEFAULT_RUBRIC = ["structural", "completeness"]`.

## File Structure

- `app/models.py` — MODIFY: add `Admissibility` model.
- `app/seed.py` — MODIFY: add `Admissibility` to `_FORCE_DELETE_MODELS`.
- `app/admissibility.py` — CREATE: `Verdict`, `Predicate` protocol, `CompletenessPredicate`, `DEFAULT_RUBRIC`, `non_admitted_output_ids` (lazy registry to avoid a circular import with structural).
- `app/structural.py` — CREATE: `evaluate_glb` (pure geometry), `upsert_verdict`, `enumerate_structural_work`, `evaluate_outputs` (fail-loud), `StructuralPredicate`.
- `scripts/score_structural.py` — CREATE: backfill driver (no VLM/render).
- `app/ingest.py` — MODIFY: best-effort structural verdict for a newly-created output.
- `app/main.py` — MODIFY: `_build_comparison` routes `_gated` through the composer.
- Tests: `tests/test_admissibility.py`, `tests/test_structural_eval.py`, `tests/test_structural_persist.py`, `tests/test_admissibility_pool.py`.

---

### Task 1: Admissibility model + seed cascade

**Files:**

- Modify: `app/models.py` (new model at end)
- Modify: `app/seed.py` (`_FORCE_DELETE_MODELS`)
- Test: `tests/test_admissibility_schema.py`

**Interfaces:**

- Produces: `Admissibility(id, output_id, predicate, admit, reason, detail_json, version, computed)`, unique `(output_id, predicate)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_admissibility_schema.py
from __future__ import annotations

from app import seed as seed_mod
from app.database import SessionLocal, init_db
from app.models import Admissibility


def setup_module(_m):
    init_db()


def test_admissibility_columns():
    cols = {c.name for c in Admissibility.__table__.columns}
    assert cols == {"id", "output_id", "predicate", "admit", "reason", "detail_json", "version", "computed"}


def test_admissibility_in_force_delete_models():
    assert Admissibility in seed_mod._FORCE_DELETE_MODELS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH="$(pwd)" .venv/bin/pytest tests/test_admissibility_schema.py -v`
Expected: FAIL (`ImportError: cannot import name 'Admissibility'`).

- [ ] **Step 3: Add the model**

At the end of `app/models.py`:

```python
class Admissibility(Base):
    """One predicate verdict for one ModelOutput — the pre-vote admissibility gate. Multiple
    rows per output (one per predicate); unique on (output_id, predicate), rescore overwrites."""

    __tablename__ = "admissibility"

    id: Mapped[int] = mapped_column(primary_key=True)
    output_id: Mapped[int] = mapped_column(ForeignKey("model_output.id"), index=True)
    predicate: Mapped[str] = mapped_column(String(32), index=True)
    admit: Mapped[bool] = mapped_column(Boolean, default=True)
    reason: Mapped[str] = mapped_column(String(64), default="")
    detail_json: Mapped[str] = mapped_column(Text, default="{}")
    version: Mapped[str] = mapped_column(String(64), default="")
    computed: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (UniqueConstraint("output_id", "predicate", name="uq_admissibility_output_predicate"),)
```

(`Boolean`, `String`, `Text`, `DateTime`, `ForeignKey`, `UniqueConstraint`, `Mapped`, `mapped_column`, `_utcnow`, `dt` are already imported in models.py. If `UniqueConstraint` is NOT in the imports, add it to the `from sqlalchemy import ...` line.)

- [ ] **Step 4: Add to the seed force-delete cascade**

In `app/seed.py`, add `Admissibility` to `_FORCE_DELETE_MODELS` BEFORE `ModelOutput` (children before parents), and import it in the `from app.models import (...)` block.

- [ ] **Step 5: Run tests to verify they pass**

Run: `PYTHONPATH="$(pwd)" .venv/bin/pytest tests/test_admissibility_schema.py tests/test_seed_force_cascade.py -v`
Expected: PASS (schema + the drift-guard now green with Admissibility covered).

- [ ] **Step 6: Commit**

```bash
git add app/models.py app/seed.py tests/test_admissibility_schema.py
git commit -m "feat(admissibility): Admissibility model + seed cascade coverage"
```

---

### Task 2: admissibility abstraction (Verdict, protocol, completeness predicate, composer)

**Files:**

- Create: `app/admissibility.py`
- Test: `tests/test_admissibility.py`

**Interfaces:**

- Consumes: `flags.excluded_output_ids_by_completeness`, `config.POOL_EXCLUDED_COMPLETENESS_CATEGORIES`.
- Produces: `Verdict(admit: bool, reason: str, detail: dict)`; `Predicate` protocol with `name: str`, `version: str`, `rejected_output_ids(db) -> set[int]`; `DEFAULT_RUBRIC: list[str]`; `non_admitted_output_ids(db, rubric=None) -> set[int]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_admissibility.py
from __future__ import annotations

import uuid

import pytest

from app import admissibility
from app.database import SessionLocal, init_db
from app.models import Completeness, Generator, ModelOutput, Task


def setup_module(_m):
    init_db()


def _output_with_category(db, category):
    g = Generator(slug=f"ad-{uuid.uuid4().hex}", name="g", kind="model", paradigm="p")
    db.add(g)
    db.flush()
    t = Task(title=f"ad-{uuid.uuid4().hex[:8]}", prompt="p", category_id=1)
    db.add(t)
    db.flush()
    o = ModelOutput(task_id=t.id, generator_id=g.id, asset_path="x.glb", asset_format="glb")
    db.add(o)
    db.flush()
    db.add(Completeness(output_id=o.id, category=category, score=0.0, scorer_version="v1"))
    db.commit()
    return o.id


def test_completeness_predicate_rejects_bad_category():
    with SessionLocal() as db:
        bad = _output_with_category(db, "fragment")
        good = _output_with_category(db, "complete")
        rejected = admissibility.non_admitted_output_ids(db, rubric=["completeness"])
        assert bad in rejected and good not in rejected


def test_empty_rubric_admits_all():
    with SessionLocal() as db:
        assert admissibility.non_admitted_output_ids(db, rubric=[]) == set()


def test_unknown_predicate_is_fail_loud():
    with SessionLocal() as db:
        with pytest.raises(KeyError):
            admissibility.non_admitted_output_ids(db, rubric=["does_not_exist"])


def test_default_rubric_is_structural_then_completeness():
    assert admissibility.DEFAULT_RUBRIC == ["structural", "completeness"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH="$(pwd)" .venv/bin/pytest tests/test_admissibility.py -v`
Expected: FAIL (`ModuleNotFoundError: app.admissibility`).

- [ ] **Step 3: Write the implementation**

```python
# app/admissibility.py
"""Pre-vote admissibility gate: an output is admitted iff every predicate in the active rubric
admits it. Predicates are pluggable (structural geometry, completeness category, ...); the pool
gate calls non_admitted_output_ids() — the single composer. Generalizes by swapping the rubric
(a list of predicate names), machinery unchanged."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy.orm import Session

from . import config, flags


@dataclass(frozen=True)
class Verdict:
    admit: bool
    reason: str = ""  # "" when admit; else a short machine code, e.g. "degenerate_bbox"
    detail: dict = field(default_factory=dict)


class Predicate(Protocol):
    name: str
    version: str

    def rejected_output_ids(self, db: Session) -> set[int]:
        """Output ids this predicate does NOT admit (precomputed source; keeps the gate O(1))."""
        ...


class CompletenessPredicate:
    name = "completeness"
    version = "completeness-v1"

    def rejected_output_ids(self, db: Session) -> set[int]:
        # Reuse the existing completeness-category exclusion verbatim (no re-scoring, no drift).
        return flags.excluded_output_ids_by_completeness(
            db, config.POOL_EXCLUDED_COMPLETENESS_CATEGORIES
        )


DEFAULT_RUBRIC: list[str] = ["structural", "completeness"]


def _registry() -> dict[str, Predicate]:
    # Lazy (function-local) import of StructuralPredicate avoids a module-level import cycle
    # (structural.py imports Verdict from this module).
    from .structural import StructuralPredicate

    return {"completeness": CompletenessPredicate(), "structural": StructuralPredicate()}


def non_admitted_output_ids(db: Session, rubric: list[str] | None = None) -> set[int]:
    """Union of rejected ids across the rubric's predicates. rubric=None -> DEFAULT_RUBRIC.
    Unknown predicate name -> KeyError (fail-loud)."""
    reg = _registry()
    names = DEFAULT_RUBRIC if rubric is None else rubric
    out: set[int] = set()
    for name in names:
        out |= reg[name].rejected_output_ids(db)
    return out
```

(Note: Task 4 adds `StructuralPredicate` to `app/structural.py`. Until then `_registry()` will fail to import it — that is why THIS task's tests only use `rubric=["completeness"]`/`[]`/`["does_not_exist"]`, never the default rubric's structural member. `test_default_rubric_is_structural_then_completeness` only reads the constant, not the registry.)

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH="$(pwd)" .venv/bin/pytest tests/test_admissibility.py -v`
Expected: FAIL on `test_unknown_predicate_is_fail_loud` only if `_registry()` raises ImportError before the KeyError. To keep this task self-contained, guard the structural import:

Replace `_registry()` body with:

```python
def _registry() -> dict[str, Predicate]:
    reg: dict[str, Predicate] = {"completeness": CompletenessPredicate()}
    try:
        from .structural import StructuralPredicate  # added in Task 4
        reg["structural"] = StructuralPredicate()
    except ImportError:
        pass
    return reg
```

Re-run: PASS (4 tests). `does_not_exist` → `reg[name]` KeyError (structural absent is fine — completeness present).

- [ ] **Step 5: Commit**

```bash
git add app/admissibility.py tests/test_admissibility.py
git commit -m "feat(admissibility): Verdict + Predicate protocol + completeness predicate + composer"
```

---

### Task 3: structural predicate — pure geometry evaluate

**Files:**

- Create: `app/structural.py` (evaluate only; persistence in Task 4)
- Test: `tests/test_structural_eval.py`

**Interfaces:**

- Consumes: `admissibility.Verdict`, `trimesh`, `numpy`.
- Produces: `evaluate_glb(path: str) -> Verdict`; constants `MIN_VERTS`, `MIN_FACES`, `MIN_EXTENT_RATIO`, `VERSION`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_structural_eval.py
from __future__ import annotations

import numpy as np
import trimesh

from app import structural


def _save(mesh, tmp_path, name="m.glb"):
    p = tmp_path / name
    mesh.export(p)
    return str(p)


def test_valid_box_admits(tmp_path):
    v = structural.evaluate_glb(_save(trimesh.creation.box((1, 1, 1)), tmp_path))
    assert v.admit and v.reason == ""


def test_single_triangle_rejected(tmp_path):
    tri = trimesh.Trimesh(vertices=[[0, 0, 0], [1, 0, 0], [0, 1, 0]], faces=[[0, 1, 2]])
    v = structural.evaluate_glb(_save(tri, tmp_path))
    assert not v.admit
    assert v.reason in ("too_small", "degenerate_bbox")  # 3 verts/1 face AND flat


def test_flat_sheet_rejected_degenerate(tmp_path):
    # A dense but perfectly flat sheet: enough verts/faces, but zero thickness.
    box = trimesh.creation.box((1, 1, 1))
    box.vertices[:, 2] = 0.0  # collapse Z
    v = structural.evaluate_glb(_save(box, tmp_path))
    assert not v.admit and v.reason == "degenerate_bbox"


def test_unreadable_rejected(tmp_path):
    p = tmp_path / "bad.glb"
    p.write_bytes(b"not a glb")
    v = structural.evaluate_glb(str(p))
    assert not v.admit and v.reason in ("unreadable", "empty")


def test_multi_component_plantlike_admits(tmp_path):
    # Two separated boxes = 2 components (plants have many detached leaves) — must ADMIT.
    a = trimesh.creation.box((1, 1, 1))
    b = trimesh.creation.box((1, 1, 1))
    b.apply_translation((5, 0, 0))
    scene = trimesh.Scene([a, b])
    v = structural.evaluate_glb(_save(scene, tmp_path, "scene.glb"))
    assert v.admit
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH="$(pwd)" .venv/bin/pytest tests/test_structural_eval.py -v`
Expected: FAIL (`ModuleNotFoundError: app.structural`).

- [ ] **Step 3: Write the implementation**

```python
# app/structural.py
"""Structural-validity predicate: pure trimesh geometry, no VLM. Rejects ONLY unambiguous
degeneracy (conservative / precision-first) — a false positive silently removes a real candidate,
so thresholds are tuned to reject the flagged broken set with ZERO false positives on good meshes."""

from __future__ import annotations

import numpy as np

from .admissibility import Verdict

VERSION = "structural-v1"

# Conservative floors. A real 3D plant mesh has thousands of verts/faces and true 3D extent;
# a degenerate output (single triangle, flat sheet, empty/corrupt) fails one of these.
MIN_VERTS = 8
MIN_FACES = 8
MIN_EXTENT_RATIO = 0.02  # smallest bbox extent / bbox diagonal; below this = a sliver/flat


def evaluate_glb(path: str) -> Verdict:
    """Load a GLB (concatenated to one mesh) and return an admissibility Verdict."""
    import trimesh  # local import: heavy

    try:
        mesh = trimesh.load(path, force="mesh")  # repo idiom (ingest._validate_mesh)
    except Exception as e:  # noqa: BLE001 — a corrupt asset is a reject, not a crash
        return Verdict(False, "unreadable", {"error": str(e)[:200]})

    verts = np.asarray(getattr(mesh, "vertices", np.empty((0, 3))), dtype=float)
    faces = getattr(mesh, "faces", None)
    nv = int(len(verts))
    nf = 0 if faces is None else int(len(faces))

    if nv == 0 or nf == 0:
        return Verdict(False, "empty", {"verts": nv, "faces": nf})
    if not np.isfinite(verts).all():
        return Verdict(False, "non_finite", {})
    if nv < MIN_VERTS or nf < MIN_FACES:
        return Verdict(False, "too_small", {"verts": nv, "faces": nf})

    extents = np.asarray(mesh.extents, dtype=float)  # bbox size (3,)
    diag = float(np.linalg.norm(extents))
    ratio = float(extents.min() / diag) if diag > 0 else 0.0
    if ratio < MIN_EXTENT_RATIO:
        return Verdict(False, "degenerate_bbox", {"extent_ratio": ratio})

    return Verdict(True, "", {"verts": nv, "faces": nf, "extent_ratio": ratio})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH="$(pwd)" .venv/bin/pytest tests/test_structural_eval.py -v`
Expected: PASS (5 tests). If `test_multi_component_plantlike_admits` fails because `force="mesh"` on a 2-box scene yields extents fine (it should admit), confirm the boxes give a true 3D bbox — they do (span 6×1×1).

- [ ] **Step 5: Commit**

```bash
git add app/structural.py tests/test_structural_eval.py
git commit -m "feat(admissibility): structural predicate geometry (conservative degeneracy checks)"
```

---

### Task 4: structural persistence + StructuralPredicate

**Files:**

- Modify: `app/structural.py` (add persistence + predicate class)
- Test: `tests/test_structural_persist.py`

**Interfaces:**

- Consumes: `Admissibility` model, `config.ASSET_DIR`, `evaluate_glb`.
- Produces: `upsert_verdict(db, output_id, predicate, verdict, version)`; `enumerate_structural_work(db) -> list[int]`; `evaluate_outputs(db, output_ids) -> dict`; `class StructuralPredicate` with `name="structural"`, `version=VERSION`, `rejected_output_ids(db) -> set[int]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_structural_persist.py
from __future__ import annotations

import uuid

import trimesh

from app import structural
from app.admissibility import Verdict
from app.config import ASSET_DIR
from app.database import SessionLocal, init_db
from app.models import Admissibility, Generator, ModelOutput, Task


def setup_module(_m):
    init_db()


def _output(db, rel_asset):
    g = Generator(slug=f"st-{uuid.uuid4().hex}", name="g", kind="model", paradigm="p")
    db.add(g)
    db.flush()
    t = Task(title=f"st-{uuid.uuid4().hex[:8]}", prompt="p", category_id=1)
    db.add(t)
    db.flush()
    o = ModelOutput(task_id=t.id, generator_id=g.id, asset_path=rel_asset, asset_format="glb")
    db.add(o)
    db.commit()
    return o.id


def _write_asset(rel, mesh):
    p = ASSET_DIR / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    mesh.export(p)


def test_upsert_overwrites():
    with SessionLocal() as db:
        oid = _output(db, "st/x.glb")
        structural.upsert_verdict(db, oid, "structural", Verdict(False, "empty", {}), "structural-v1")
        structural.upsert_verdict(db, oid, "structural", Verdict(True, "", {}), "structural-v1")
        db.commit()
        rows = db.query(Admissibility).filter_by(output_id=oid, predicate="structural").all()
        assert len(rows) == 1 and rows[0].admit is True


def test_evaluate_outputs_and_rejected():
    with SessionLocal() as db:
        rel_bad = f"st/{uuid.uuid4().hex}.glb"
        rel_good = f"st/{uuid.uuid4().hex}.glb"
        tri = trimesh.Trimesh(vertices=[[0, 0, 0], [1, 0, 0], [0, 1, 0]], faces=[[0, 1, 2]])
        _write_asset(rel_bad, tri)
        _write_asset(rel_good, trimesh.creation.box((1, 1, 1)))
        bad = _output(db, rel_bad)
        good = _output(db, rel_good)
        res = structural.evaluate_outputs(db, [bad, good])
        db.commit()
        assert res["scored"] == 2
        rejected = structural.StructuralPredicate().rejected_output_ids(db)
        assert bad in rejected and good not in rejected


def test_enumerate_skips_current_version():
    with SessionLocal() as db:
        oid = _output(db, "st/y.glb")
        assert oid in structural.enumerate_structural_work(db)
        structural.upsert_verdict(db, oid, "structural", Verdict(True, "", {}), structural.VERSION)
        db.commit()
        assert oid not in structural.enumerate_structural_work(db)


def test_evaluate_outputs_fail_loud_per_output():
    with SessionLocal() as db:
        missing = _output(db, "st/does-not-exist.glb")  # no file on disk
        rel_good = f"st/{uuid.uuid4().hex}.glb"
        _write_asset(rel_good, trimesh.creation.box((1, 1, 1)))
        good = _output(db, rel_good)
        res = structural.evaluate_outputs(db, [missing, good])
        db.commit()
        # A missing/unreadable asset yields a reject verdict (not a crash); the loop continues.
        assert res["scored"] >= 1
        assert missing in structural.StructuralPredicate().rejected_output_ids(db)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH="$(pwd)" .venv/bin/pytest tests/test_structural_persist.py -v`
Expected: FAIL (`AttributeError: module 'app.structural' has no attribute 'upsert_verdict'`).

- [ ] **Step 3: Add persistence + predicate to `app/structural.py`**

Append to `app/structural.py`:

```python
import json
import os

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config
from .models import Admissibility, ModelOutput


def upsert_verdict(db: Session, output_id: int, predicate: str, verdict: Verdict, version: str):
    """Insert or overwrite the single (output_id, predicate) admissibility row. Caller commits."""
    row = (
        db.query(Admissibility).filter_by(output_id=output_id, predicate=predicate).one_or_none()
    )
    if row is None:
        row = Admissibility(output_id=output_id, predicate=predicate)
        db.add(row)
    row.admit = verdict.admit
    row.reason = verdict.reason
    row.detail_json = json.dumps(verdict.detail)
    row.version = version
    return row


def enumerate_structural_work(db: Session) -> list[int]:
    """Output ids lacking a current-VERSION structural verdict (non-gold)."""
    have = {
        oid
        for (oid,) in db.execute(
            select(Admissibility.output_id).where(
                Admissibility.predicate == "structural", Admissibility.version == VERSION
            )
        ).all()
    }
    all_ids = [oid for (oid,) in db.execute(select(ModelOutput.id).where(ModelOutput.is_gold.is_(False))).all()]
    return [oid for oid in all_ids if oid not in have]


def _asset_path(output: ModelOutput) -> str:
    return os.path.join(str(config.ASSET_DIR), output.asset_path)


def evaluate_outputs(db: Session, output_ids: list[int]) -> dict:
    """Evaluate each output's GLB and upsert its structural verdict. Fail-loud per output:
    a missing/unreadable asset yields a reject verdict (recorded), never aborts the batch.
    Caller commits."""
    scored = errors = 0
    seen: set[int] = set()
    for oid in output_ids:
        if oid in seen:
            continue
        seen.add(oid)
        out = db.get(ModelOutput, oid)
        if out is None:
            errors += 1
            continue
        try:
            verdict = evaluate_glb(_asset_path(out))
        except Exception as e:  # noqa: BLE001 — record a reject, keep going
            verdict = Verdict(False, "unreadable", {"error": str(e)[:200]})
            errors += 1
        upsert_verdict(db, oid, "structural", verdict, VERSION)
        scored += 1
    return {"scored": scored, "errors": errors}


class StructuralPredicate:
    name = "structural"
    version = VERSION

    def rejected_output_ids(self, db: Session) -> set[int]:
        return {
            oid
            for (oid,) in db.execute(
                select(Admissibility.output_id).where(
                    Admissibility.predicate == "structural", Admissibility.admit.is_(False)
                )
            ).all()
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH="$(pwd)" .venv/bin/pytest tests/test_structural_persist.py tests/test_admissibility.py -v`
Expected: PASS (persistence 4 + admissibility 4; the default rubric now resolves structural).

- [ ] **Step 5: Commit**

```bash
git add app/structural.py tests/test_structural_persist.py
git commit -m "feat(admissibility): structural persistence (upsert/enumerate/evaluate) + StructuralPredicate"
```

---

### Task 5: backfill script + ingest hook

**Files:**

- Create: `scripts/score_structural.py`
- Modify: `app/ingest.py` (after the `db.flush()` at line ~225)
- Test: `tests/test_structural_ingest.py`

**Interfaces:**

- Consumes: `structural.enumerate_structural_work`, `structural.evaluate_outputs`, `structural.StructuralPredicate`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_structural_ingest.py
from __future__ import annotations

import io
import uuid

import trimesh

from app import ingest, structural
from app.database import SessionLocal, init_db
from app.models import Admissibility, Category, Task


def setup_module(_m):
    init_db()


def _glb_bytes(mesh):
    buf = io.BytesIO()
    mesh.export(buf, file_type="glb")
    return buf.getvalue()


def test_ingest_creates_structural_verdict():
    with SessionLocal() as db:
        cat = Category(slug=f"si-{uuid.uuid4().hex[:8]}", name="C")
        db.add(cat)
        db.flush()
        t = Task(category_id=cat.id, title=f"si-{uuid.uuid4().hex[:8]}", prompt="p")
        db.add(t)
        db.commit()
        # register_output(db, task_id, generator_slug, data, ext=...) upserts the generator itself
        # and creates the ModelOutput (app/ingest.py:172).
        out, created = ingest.register_output(
            db,
            task_id=t.id,
            generator_slug=f"si-{uuid.uuid4().hex}",
            data=_glb_bytes(trimesh.creation.box((1, 1, 1))),
            ext="glb",
        )
        db.commit()
        row = db.query(Admissibility).filter_by(output_id=out.id, predicate="structural").one_or_none()
        assert row is not None and row.admit is True
```

The ingest hook (Step 3) goes at the end of `register_output` (`app/ingest.py:172`), after its `db.flush()` and before `return output, True`.

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH="$(pwd)" .venv/bin/pytest tests/test_structural_ingest.py -v`
Expected: FAIL (no Admissibility row created at ingest).

- [ ] **Step 3: Add the ingest hook**

In `app/ingest.py`, after the new output is added + flushed (line ~225, `db.flush(); return output, True`), before the return, add a best-effort structural verdict:

```python
    db.flush()
    # Best-effort structural admissibility verdict so new assets are gated from first appearance.
    # Guarded: a bad asset records a reject, never breaks ingest.
    try:
        from . import structural

        structural.evaluate_outputs(db, [output.id])
    except Exception:  # noqa: BLE001 — ingest must not fail on the admissibility hook
        pass
    return output, True
```

- [ ] **Step 4: Write the backfill script**

```python
# scripts/score_structural.py
"""Backfill structural admissibility verdicts for every output lacking a current-version one.
Pure trimesh geometry — no VLM, no browser. NEVER point BIO3D_DATABASE_URL at the study DB;
use a copy. Usage: PYTHONPATH=. BIO3D_DATABASE_URL=sqlite:///<copy> .venv/bin/python scripts/score_structural.py"""

from __future__ import annotations

import sys

from app import structural
from app.database import SessionLocal, init_db


def main() -> int:
    init_db()
    with SessionLocal() as db:
        work = structural.enumerate_structural_work(db)
        print(f"structural backfill: {len(work)} outputs to evaluate", flush=True)
        res = structural.evaluate_outputs(db, work)
        db.commit()
        rejected = len(structural.StructuralPredicate().rejected_output_ids(db))
        print(f"done: scored={res['scored']} errors={res['errors']} total_rejected={rejected}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run test to verify it passes**

Run: `PYTHONPATH="$(pwd)" .venv/bin/pytest tests/test_structural_ingest.py -v`
Expected: PASS. Then a smoke of the script against a throwaway DB:
Run: `BIO3D_DATABASE_URL="sqlite:///tmp/bio3d_test_struct.db" PYTHONPATH="$(pwd)" .venv/bin/python scripts/score_structural.py`
Expected: prints a backfill summary, exit 0.

- [ ] **Step 6: Commit**

```bash
git add scripts/score_structural.py app/ingest.py tests/test_structural_ingest.py
git commit -m "feat(admissibility): structural backfill script + ingest hook"
```

---

### Task 6: wire the pool gate through the composer

**Files:**

- Modify: `app/main.py` (`_build_comparison`, the `_gated` precompute ~line 276)
- Test: `tests/test_admissibility_pool.py`

**Interfaces:**

- Consumes: `admissibility.non_admitted_output_ids`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_admissibility_pool.py
from __future__ import annotations

import uuid

from app.admissibility import Verdict
from app.database import SessionLocal
from app.main import _build_comparison
from app.models import Category, Comparison, Generator, ModelOutput, Task
from app import structural


def _task_with_two(db):
    cat = Category(slug=f"ap-{uuid.uuid4().hex[:8]}", name="C")
    db.add(cat)
    db.flush()
    from app.seed import seed_all  # ensure an 'overall' criterion exists

    seed_all(force=True)
    cat = Category(slug=f"ap-{uuid.uuid4().hex[:8]}", name="C")
    db.add(cat)
    db.flush()
    t = Task(category_id=cat.id, title=f"ap-{uuid.uuid4().hex[:8]}", prompt="p")
    db.add(t)
    db.flush()
    outs = []
    for _ in range(3):
        g = Generator(slug=f"ap-{uuid.uuid4().hex}", name="g", kind="model", paradigm="same")
        db.add(g)
        db.flush()
        o = ModelOutput(task_id=t.id, generator_id=g.id, asset_path="x.glb", asset_format="glb")
        db.add(o)
        db.flush()
        outs.append(o)
    db.commit()
    return cat, outs


def test_structurally_rejected_output_never_served():
    with SessionLocal() as db:
        cat, outs = _task_with_two(db)
        structural.upsert_verdict(db, outs[0].id, "structural", Verdict(False, "empty", {}), structural.VERSION)
        db.commit()
        for _ in range(30):
            payload = _build_comparison(db, f"s-{uuid.uuid4().hex}", None, cat.slug)
            if payload is None:
                continue
            c = db.get(Comparison, payload["comparison_id"])
            assert outs[0].id not in {c.output_a_id, c.output_b_id}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH="$(pwd)" .venv/bin/pytest tests/test_admissibility_pool.py -v`
Expected: FAIL (the structurally-rejected output is still served — gate not wired).

- [ ] **Step 3: Wire the composer into `_build_comparison`**

In `app/main.py`, the `_gated` precompute currently reads (line ~275-277):

```python
    from . import flags

    ...
    _gated = flags.excluded_output_ids_by_completeness(
        db, config.POOL_EXCLUDED_COMPLETENESS_CATEGORIES
    )
```

Replace the `_gated` assignment with the composer (structural ∪ completeness), and drop the now-unused local `flags` import if it is used only for this:

```python
    from . import admissibility

    ...
    _gated = admissibility.non_admitted_output_ids(db)  # structural ∪ completeness
```

Leave `_vote_excluded` unchanged — it already does `or o.id in _gated`, and the reference-scan/untextured/`hidden_at` checks stay inline. The SAME `_vote_excluded` is still passed to both `pick_task` and `pick_pair` (parity preserved).

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH="$(pwd)" .venv/bin/pytest tests/test_admissibility_pool.py tests/test_pool_autogate.py tests/test_matchmaking_exclude.py -v`
Expected: PASS — structural rejects gated, completeness still gated (test_pool_autogate), pick_task parity holds (no 404 regression).

- [ ] **Step 5: Run the full suite**

Run: `PYTHONPATH="$(pwd)" .venv/bin/pytest -q`
Expected: PASS — 660 baseline + new tests, 8 skipped, 0 failed.

- [ ] **Step 6: Commit**

```bash
git add app/main.py tests/test_admissibility_pool.py
git commit -m "feat(admissibility): route vote-pool gate through the predicate composer"
```

---

## Real-execution acceptance gate (controller-run after Task 6, before merge)

NOT an SDD task — the controller runs this against a COPY of the study DB with real GLBs:

1. `cp data/study/arena-study.db <copy>`; boot self-heals schema.
2. `BIO3D_DATABASE_URL="sqlite:///<copy>" PYTHONPATH="$(pwd)" .venv/bin/python scripts/score_structural.py`.
3. Cross-tabulate structural verdicts vs (a) the 32 audit flags and (b) a sample of "complete"/good outputs.
4. **Merge-blocking:** ZERO good/"complete" outputs rejected (false-positive rate 0). Report recall on the flagged degenerate subset honestly (structural catches geometric degeneracy only).
5. Write `docs/results/2026-07-03-structural-admissibility-results.md`. If any good output is rejected, tighten `MIN_EXTENT_RATIO`/floors and re-run before merge.

## Self-Review

**Spec coverage:** abstraction (T2) ✅; Admissibility model + cascade (T1) ✅; structural evaluate (T3) + persistence/predicate (T4) ✅; backfill + ingest hook (T5) ✅; completeness predicate reuse (T2 `CompletenessPredicate`) ✅; pool wiring replacing main.py:276 (T6) ✅; precision-first zero-FP gate (real-execution acceptance) ✅; generalization seam (rubric = list of names, T2) ✅; drift-guard covers Admissibility (T1) ✅.

**Placeholder scan:** none — every code step has full code. Task 5's ingest-entrypoint note directs matching the live signature (grounded fact, not a placeholder).

**Type consistency:** `Verdict(admit, reason, detail)` consistent T2→T3→T4. `rejected_output_ids(db) -> set[int]` consistent across CompletenessPredicate/StructuralPredicate/protocol. `non_admitted_output_ids(db, rubric=None)` consistent T2→T6. `evaluate_glb(path) -> Verdict` T3→T4. `upsert_verdict(db, output_id, predicate, verdict, version)` T4→T5. `VERSION`/`DEFAULT_RUBRIC` names consistent.

**Note for implementer (Task 5):** verify the live `app/ingest.py` public entrypoint name/signature before writing the ingest test — the assertion (a structural Admissibility row exists for a newly-ingested output) is the contract, not the exact call.
