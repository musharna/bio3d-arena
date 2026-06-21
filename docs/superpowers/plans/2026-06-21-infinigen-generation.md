# Infinigen Procedural Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Infinigen procedural plants to the tomato spotlight as a distinct "Procedural" generator class, ingesting a generated bush (OBJ→GLB) via the existing register_output + isolated-scoring pipeline.

**Architecture:** A new `source_class` value `"procedural"` + a spotlight group surface the category. An ingest adapter (`scripts/generate_infinigen.py`) shells out to a separate `infinigen` conda env to generate a `BushFactory` asset headlessly, converts the OBJ via the existing `mesh_convert.to_glb` bridge, and ingests it as `source="infinigen"`. The Infinigen install + real generation run are an operational step, not a build task.

**Tech Stack:** Python, trimesh (already a dep, for OBJ→GLB + test fixtures), FastAPI/Jinja2, SQLAlchemy, the AgriGen recon scorer (`:8077`). Infinigen runs in its OWN conda env (Python 3.11), invoked by subprocess — NOT a dependency of the app's `.venv`.

## Global Constraints

- New generator class `"procedural"` for `source == "infinigen"`; a "Procedural (Infinigen)" spotlight group. Existing ai/scan/found behavior unchanged.
- OBJ→GLB reuses `app/mesh_convert.to_glb(path, max_faces=...)` (live signature: `to_glb(src_path: str, *, max_faces: int | None = None) -> bytes`) — NO new converter.
- Reuse `register_output` (`app/ingest.py:172`): per-object commit; provenance (`source`/`license`/`attribution`/`external_url`) set AFTER register, committed BEFORE scoring; scoring isolated/best-effort — a scoring failure NEVER drops the hosted object.
- Infinigen provenance: `license="BSD-3-Clause (Infinigen, Princeton VL)"`, `external_url="https://github.com/princeton-vl/infinigen"`.
- The tomato Task title (verbatim): `Solanum lycopersicum — single-image → 3D reconstruction`.
- Do NOT touch `/home/mjarnold/agrigen`. Infinigen is NOT added to `requirements.txt` (it lives in its own conda env).
- Infinigen install + real generation = operational (NOT a build task); the build ships synthetic-tested.

---

## File Structure

- **Modify** `app/sourcing.py` — `source_class` returns `"procedural"` for `"infinigen"`.
- **Modify** `app/templates/spotlight.html` — add the "Procedural (Infinigen)" group.
- **Modify** `tests/test_source_class.py` + `tests/test_spotlight_scan_group.py` — category + render tests.
- **Create** `scripts/generate_infinigen.py` — `ingest_infinigen` core + `main()`.
- **Create** `tests/test_generate_infinigen.py` — fixture-OBJ ingest tests.

---

### Task 1: `procedural` category + spotlight group

**Files:**

- Modify: `app/sourcing.py` (`source_class`)
- Modify: `app/templates/spotlight.html` (group block)
- Test: `tests/test_source_class.py`, `tests/test_spotlight_scan_group.py`

**Interfaces:**

- Produces: `source_class("infinigen") == "procedural"`; the spotlight renders a "Procedural (Infinigen)" group for cards whose `cls == "procedural"`.

- [ ] **Step 1: Write the failing unit test** (append to `tests/test_source_class.py`)

```python
def test_source_class_infinigen_is_procedural():
    from app.sourcing import source_class

    assert source_class("infinigen") == "procedural"
    assert source_class("api:tripo") == "ai"  # unchanged
    assert source_class("plant3d") == "scan"  # unchanged
    assert source_class("objaverse") == "found"  # unchanged
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_source_class.py::test_source_class_infinigen_is_procedural -v`
Expected: FAIL — `source_class("infinigen")` returns `"found"`, not `"procedural"`.

- [ ] **Step 3: Implement source_class** — in `app/sourcing.py`, change `source_class` to:

```python
def source_class(source: str | None) -> str:
    """Group key for the spotlight: 'ai' (our recon — local or via an image-to-3D API),
    'procedural' (rule-based generators like Infinigen), 'scan' (real scan dataset),
    'found' (artist repos like Objaverse/Sketchfab)."""
    if source == "bio3d-arena" or (source or "").startswith("api:"):
        return "ai"
    if source == "infinigen":
        return "procedural"
    if source in SCAN_SOURCES:
        return "scan"
    return "found"
```

- [ ] **Step 4: Add the spotlight group** — in `app/templates/spotlight.html`, the group block currently reads:

```html
{% set ai = s.models | selectattr('cls', 'equalto', 'ai') | list %} {% set scan
= s.models | selectattr('cls', 'equalto', 'scan') | list %} {% set found =
s.models | selectattr('cls', 'equalto', 'found') | list %} {% set groups = [
('AI reconstruction', ai), ('Real scan — whole plant', scan |
selectattr('depiction', 'equalto', 'whole_plant') | list),
```

Change it to add the procedural set and group (a "Procedural (Infinigen)" group right after AI reconstruction — both are generated outputs):

```html
{% set ai = s.models | selectattr('cls', 'equalto', 'ai') | list %} {% set
procedural = s.models | selectattr('cls', 'equalto', 'procedural') | list %} {%
set scan = s.models | selectattr('cls', 'equalto', 'scan') | list %} {% set
found = s.models | selectattr('cls', 'equalto', 'found') | list %} {% set groups
= [ ('AI reconstruction', ai), ('Procedural (Infinigen)', procedural), ('Real
scan — whole plant', scan | selectattr('depiction', 'equalto', 'whole_plant') |
list),
```

(Leave the rest of the `groups` list unchanged. The `{% for gname, gmodels in groups %}` loop already renders only non-empty groups, so an empty procedural group shows nothing.)

- [ ] **Step 5: Write the failing render test** (append to `tests/test_spotlight_scan_group.py`; it already imports trimesh, TestClient, app, ingest, spotlight, SessionLocal, Category, Task)

```python
def test_procedural_card_renders_under_procedural_group(monkeypatch):
    # monkeypatch FIRST — build_spotlight("proc") returns None (crashing on ["models"])
    # if the "proc" slug is not registered before the call.
    monkeypatch.setattr(
        spotlight,
        "SPOTLIGHTS",
        [{"slug": "proc", "task_title": "Proc Subject", "featured": True,
          "order": 0, "blurb": "b", "reference_image": None}],
    )
    db = SessionLocal()
    try:
        cat = db.query(Category).filter_by(slug="plants").first() or Category(
            slug="plants", name="Plants"
        )
        db.add(cat)
        db.flush()
        task = Task(category_id=cat.id, title="Proc Subject", prompt="p")
        db.add(task)
        db.flush()
        glb = trimesh.creation.box().export(file_type="glb")
        out, _ = ingest.register_output(
            db,
            task_id=task.id,
            generator_slug="infinigen",
            generator_name="Infinigen",
            data=glb,
            ext="glb",
            title="bush_0",
            meta={"depiction": "whole_plant", "factory": "BushFactory", "render": "mesh"},
        )
        out.source = "infinigen"
        db.commit()
        model = spotlight.build_spotlight(db, "proc")["models"][0]
        assert model["cls"] == "procedural"
    finally:
        db.close()

    page = TestClient(app).get("/spotlight/proc")
    assert page.status_code == 200
    assert "Procedural (Infinigen)" in page.text
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_source_class.py tests/test_spotlight_scan_group.py -v`
Expected: PASS (the new tests + all existing ones).

- [ ] **Step 7: Commit**

```bash
git add app/sourcing.py app/templates/spotlight.html tests/test_source_class.py tests/test_spotlight_scan_group.py
git commit -m "feat(spotlight): procedural source class + Procedural (Infinigen) group"
```

---

### Task 2: Infinigen ingest adapter — `scripts/generate_infinigen.py`

**Files:**

- Create: `scripts/generate_infinigen.py`
- Test: `tests/test_generate_infinigen.py`

**Interfaces:**

- Consumes: `app.ingest.register_output`; `app.mesh_convert.to_glb` + `MeshConvertError`; `app.recon_service.score_and_store` (in main()).
- Produces: `ingest_infinigen(db, obj_paths, *, to_glb, score_fn=None, factory="BushFactory", task_title=TOMATO_TITLE, limit=10) -> dict` with keys `hosted`, `skipped`, `errors`, `by_factory`.

- [ ] **Step 1: Write the failing tests** (`tests/test_generate_infinigen.py`)

```python
import json

import trimesh
from sqlalchemy import select

from app import sourcing
from app.database import SessionLocal, init_db
from app.mesh_convert import MeshConvertError
from app.models import Category, ModelOutput, Task
from scripts.generate_infinigen import ingest_infinigen

TOMATO = "Solanum lycopersicum — single-image → 3D reconstruction"


def setup_module(_m):
    init_db()


def _tomato_task(db):
    cat = db.query(Category).filter_by(slug="plants").first() or Category(
        slug="plants", name="Plants"
    )
    db.add(cat)
    db.flush()
    db.add(Task(category_id=cat.id, title=TOMATO, prompt="p"))
    db.commit()


def test_ingest_infinigen_hosts_procedural(tmp_path):
    db = SessionLocal()
    try:
        _tomato_task(db)
        obj = tmp_path / "bush_0.obj"
        trimesh.creation.box().export(str(obj))  # a real OBJ with faces

        def fake_to_glb(path):
            return trimesh.load(path, force="mesh").export(file_type="glb")

        report = ingest_infinigen(db, [str(obj)], to_glb=fake_to_glb)
        assert report["hosted"] == 1
        out = db.execute(
            select(ModelOutput).where(ModelOutput.source == "infinigen")
        ).scalars().one()
        assert sourcing.source_class(out.source) == "procedural"
        assert "BSD-3" in out.license
        assert out.external_url and "infinigen" in out.external_url
        assert json.loads(out.meta_json)["factory"] == "BushFactory"
    finally:
        db.close()


def test_ingest_infinigen_skips_unconvertible(tmp_path):
    db = SessionLocal()
    try:
        _tomato_task(db)

        def raising_to_glb(path):
            raise MeshConvertError("point cloud / no faces")

        report = ingest_infinigen(db, [str(tmp_path / "x.obj")], to_glb=raising_to_glb)
        assert report["skipped"] == 1
        assert report["hosted"] == 0
    finally:
        db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_generate_infinigen.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.generate_infinigen'`

- [ ] **Step 3: Implement `scripts/generate_infinigen.py`**

```python
"""Generate procedural plants with Infinigen and ingest them as 'Procedural' outputs.
`ingest_infinigen` is the testable core (OBJ->GLB + scorer injected); `main()` shells out
to a separate `infinigen` conda env to generate, then collects + ingests. Commits per object.
Infinigen is NOT a dependency of this app's venv — it runs in its own Python-3.11 env.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app import ingest  # noqa: E402
from app.mesh_convert import MeshConvertError  # noqa: E402
from app.models import Task  # noqa: E402

TOMATO_TITLE = "Solanum lycopersicum — single-image → 3D reconstruction"
INFINIGEN_LICENSE = "BSD-3-Clause (Infinigen, Princeton VL)"
INFINIGEN_URL = "https://github.com/princeton-vl/infinigen"


def ingest_infinigen(
    db, obj_paths, *, to_glb, score_fn=None, factory="BushFactory",
    task_title=TOMATO_TITLE, limit=10,
) -> dict:
    task = db.execute(select(Task).where(Task.title == task_title)).scalars().first()
    if task is None:
        raise RuntimeError(f"subject task not found: {task_title!r}")
    report = {"hosted": 0, "skipped": 0, "errors": 0, "by_factory": {}}
    for path in list(obj_paths)[:limit]:
        asset_id = Path(path).stem
        try:
            try:
                glb = to_glb(path)
            except MeshConvertError as e:
                print(f"  skip {asset_id}: {e}")
                report["skipped"] += 1
                continue
            out, _created = ingest.register_output(
                db,
                task_id=task.id,
                generator_slug="infinigen",
                generator_name="Infinigen",
                data=glb,
                ext="glb",
                title=asset_id,
                meta={"depiction": "whole_plant", "factory": factory, "render": "mesh"},
            )
            out.source = "infinigen"
            out.license = INFINIGEN_LICENSE
            out.attribution = f"Infinigen procedural ({factory})"
            out.external_url = INFINIGEN_URL
            db.commit()  # provenance committed → hosted
            report["hosted"] += 1
            report["by_factory"][factory] = report["by_factory"].get(factory, 0) + 1
            if score_fn is not None:
                try:
                    score_fn(db, out)
                    db.commit()
                except Exception as e:  # noqa: BLE001 — scoring best-effort; object stays hosted
                    print(f"  score failed for {out.id}: {e}")
                    try:
                        db.rollback()
                    except Exception:  # noqa: BLE001
                        pass
        except Exception as e:  # noqa: BLE001 — one bad asset never aborts the batch
            print(f"  error {asset_id}: {e}")
            report["errors"] += 1
            db.rollback()
    return report


def main() -> int:
    import argparse
    import os
    import subprocess
    import tempfile

    from app import recon_service
    from app.database import SessionLocal
    from app.mesh_convert import to_glb as _to_glb

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--factory", default="BushFactory")
    ap.add_argument("-n", type=int, default=3)
    ap.add_argument(
        "--env-python",
        default=os.environ.get("INFINIGEN_PYTHON", ""),
        help="path to the infinigen conda env python (or set INFINIGEN_PYTHON)",
    )
    ap.add_argument("--max-faces", type=int, default=150_000)
    ap.add_argument("--no-score", action="store_true")
    args = ap.parse_args()

    env_python = args.env_python or "python"
    out_dir = tempfile.mkdtemp(prefix="infinigen_")
    # Verify the exact flags against `generate_individual_assets --help` at run time; this is
    # the research-confirmed headless geometry-only invocation.
    cmd = [
        env_python, "-m", "infinigen_examples.generate_individual_assets",
        "--output_folder", out_dir, "-f", args.factory, "-n", str(args.n),
        "--render", "none", "--export", "obj",
    ]
    print("running:", " ".join(cmd))
    try:
        subprocess.run(cmd, check=True, timeout=1800)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(
            f"infinigen generation failed: {e} — is the infinigen env installed? "
            f"(set --env-python or INFINIGEN_PYTHON to its conda-env python)"
        )
        return 1
    objs = sorted(str(p) for p in Path(out_dir).rglob("*.obj"))
    if not objs:
        print(f"no .obj produced under {out_dir}")
        return 1

    max_faces = args.max_faces or None

    def to_glb(path: str) -> bytes:
        return _to_glb(path, max_faces=max_faces)

    db = SessionLocal()
    try:
        report = ingest_infinigen(
            db, objs, to_glb=to_glb,
            score_fn=None if args.no_score else recon_service.score_and_store,
            factory=args.factory,
        )
        print(report)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_generate_infinigen.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Run the full suite + commit**

```bash
.venv/bin/python -m pytest -q   # expect: all pass
git add scripts/generate_infinigen.py tests/test_generate_infinigen.py
git commit -m "feat(infinigen): ingest adapter — procedural plants as Procedural outputs"
```

---

## Out of scope (do NOT build here — operational, post-build)

- **Infinigen install + real generation:** `conda create -n infinigen python=3.11`, clone
  `princeton-vl/infinigen`, `INFINIGEN_MINIMAL_INSTALL=True pip install -e .`, then
  `INFINIGEN_PYTHON=<env python> .venv/bin/python scripts/generate_infinigen.py -f BushFactory -n 3`.
  Heavy install → submit via jobd. Then render thumbnails + run the independent-critic gate.
  Verify the exact `generate_individual_assets` flags against `--help` at this step.
- Tomato-tuning (compositing `fruits`, parameter-tuning, authoring a tomato procedural).
- Other factories (TreeFactory/FlowerFactory).

## Notes for the implementer

- `mesh_convert.to_glb` already decimates above `max_faces` and raises `MeshConvertError` on a
  faces-less file — reuse it; do not write a new converter.
- The shared file-backed test DB persists rows across tests; `.one()` on `source=="infinigen"`
  is safe in `test_ingest_infinigen_hosts_procedural` only because no other test commits an
  `infinigen` row — keep it that way (the skip test never hosts).
- Never add Infinigen to `requirements.txt`; it lives in its own conda env invoked by subprocess.
