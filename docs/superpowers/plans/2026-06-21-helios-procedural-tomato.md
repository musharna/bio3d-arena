# Helios Procedural Tomato Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ingest a Helios-generated procedural tomato as a `procedural:helios` entry in the spotlight's Procedural column, scored and critic-gated.

**Architecture:** Generalize `source_class` to a `procedural:*` prefix, then add a `generate_helios.py` ingest adapter that mirrors the merged `generate_infinigen.py` (OBJ→GLB via the existing `mesh_convert.to_glb`, register as `source="procedural:helios"`, isolated scoring). The Helios C++ build + tomato generation is the operational build-gate, run by the controller before the live ingest — NOT a code task.

**Tech Stack:** Python, trimesh (test OBJ fixtures), SQLAlchemy, the AgriGen recon scorer (`:8077`). Helios is a separate C++/CMake build under `~/Helios`, invoked by subprocess — NOT an app `.venv` dep.

## Global Constraints

- `source_class` returns `"procedural"` for `source == "infinigen"` OR `source.startswith("procedural:")`. Existing ai/scan/found/api behavior unchanged.
- OBJ→GLB reuses `app/mesh_convert.to_glb(src_path, *, max_faces=None)` (live signature) — NO new converter.
- Reuse `register_output` (`app/ingest.py:172`): per-object commit; provenance set AFTER register, committed BEFORE scoring; scoring isolated/best-effort with GUARDED inner AND outer rollback — a scoring failure never drops the hosted object.
- Helios provenance: `source="procedural:helios"`, `license="GPL-2.0 (Helios, UC Davis Bailey Lab)"`, `external_url="https://github.com/PlantSimulationLab/Helios"`.
- The tomato Task title (verbatim): `Solanum lycopersicum — single-image → 3D reconstruction`.
- Do NOT touch `/home/mjarnold/agrigen`. Helios is NOT added to `requirements.txt`.
- Test read-backs MUST filter on a UNIQUE label (a per-test `variant`), never a bare `source=="procedural:helios"` `.one()` — the shared file-backed test DB collides otherwise (the merged Infinigen increment's Critical finding).
- Helios build + tomato generation = operational build-gate (NOT a build task); the code ships synthetic-tested.

## File Structure

- **Modify** `app/sourcing.py` — `source_class` `procedural:*` prefix.
- **Modify** `tests/test_source_class.py` — procedural prefix test.
- **Create** `scripts/generate_helios.py` — `ingest_helios` core + `main()`.
- **Create** `tests/test_generate_helios.py` — fixture-OBJ ingest tests.

---

### Task 1: `source_class` recognizes `procedural:*`

**Files:**

- Modify: `app/sourcing.py` (`source_class`)
- Test: `tests/test_source_class.py`

**Interfaces:**

- Produces: `source_class("procedural:helios") == "procedural"` (and any `procedural:*`); `"infinigen"` still `"procedural"`; ai/scan/found unchanged.

- [ ] **Step 1: Write the failing test** (append to `tests/test_source_class.py`)

```python
def test_source_class_procedural_prefix():
    from app.sourcing import source_class

    assert source_class("procedural:helios") == "procedural"
    assert source_class("procedural:agrigen") == "procedural"
    assert source_class("infinigen") == "procedural"  # unchanged
    assert source_class("api:tripo") == "ai"  # unchanged
    assert source_class("plant3d") == "scan"  # unchanged
    assert source_class("objaverse") == "found"  # unchanged
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_source_class.py::test_source_class_procedural_prefix -v`
Expected: FAIL — `source_class("procedural:helios")` returns `"found"`.

- [ ] **Step 3: Implement** — in `app/sourcing.py`, change the procedural line:

```python
def source_class(source: str | None) -> str:
    """Group key for the spotlight: 'ai' (our recon — local or via an image-to-3D API),
    'procedural' (rule-based generators: Infinigen, Helios, AgriGen, L-Py, ...), 'scan'
    (real scan dataset), 'found' (artist repos like Objaverse/Sketchfab)."""
    if source == "bio3d-arena" or (source or "").startswith("api:"):
        return "ai"
    if source == "infinigen" or (source or "").startswith("procedural:"):
        return "procedural"
    if source in SCAN_SOURCES:
        return "scan"
    return "found"
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_source_class.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/sourcing.py tests/test_source_class.py
git commit -m "feat(sourcing): source_class recognizes the procedural:* prefix"
```

---

### Task 2: Helios ingest adapter — `scripts/generate_helios.py`

**Files:**

- Create: `scripts/generate_helios.py`
- Test: `tests/test_generate_helios.py`

**Interfaces:**

- Consumes: `app.ingest.register_output`; `app.mesh_convert.to_glb` + `MeshConvertError`; `app.recon_service.score_and_store` (in main()); `app.sourcing.source_class` (Task 1, for the test).
- Produces: `ingest_helios(db, obj_paths, *, to_glb, score_fn=None, variant="tomato", task_title=TOMATO_TITLE, limit=10) -> dict` with keys `hosted`, `skipped`, `errors`, `by_variant`.

- [ ] **Step 1: Write the failing tests** (`tests/test_generate_helios.py`)

```python
import json

import trimesh
from sqlalchemy import select

from app import sourcing
from app.database import SessionLocal, init_db
from app.mesh_convert import MeshConvertError
from app.models import Category, ModelOutput, Task
from scripts.generate_helios import ingest_helios

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


def test_ingest_helios_hosts_procedural(tmp_path):
    db = SessionLocal()
    try:
        _tomato_task(db)
        obj = tmp_path / "tomato_0.obj"
        trimesh.creation.box().export(str(obj))  # a real OBJ with faces

        def fake_to_glb(path):
            return trimesh.load(path, force="mesh").export(file_type="glb")

        # unique variant → unique attribution for the read-back (shared file-backed test DB)
        report = ingest_helios(db, [str(obj)], to_glb=fake_to_glb, variant="tomatoHost")
        assert report["hosted"] == 1
        out = (
            db.execute(select(ModelOutput).where(ModelOutput.attribution.contains("tomatoHost")))
            .scalars()
            .one()
        )
        assert out.source == "procedural:helios"
        assert sourcing.source_class(out.source) == "procedural"
        assert "GPL-2.0" in out.license
        assert out.external_url and "Helios" in out.external_url
        assert json.loads(out.meta_json)["variant"] == "tomatoHost"
    finally:
        db.close()


def test_ingest_helios_skips_unconvertible(tmp_path):
    db = SessionLocal()
    try:
        _tomato_task(db)

        def raising_to_glb(path):
            raise MeshConvertError("no faces")

        report = ingest_helios(db, [str(tmp_path / "x.obj")], to_glb=raising_to_glb)
        assert report["skipped"] == 1
        assert report["hosted"] == 0
    finally:
        db.close()


def test_ingest_helios_scoring_failure_keeps_hosted_object(tmp_path):
    """A scoring failure rolls back only the metric — the hosted object survives, hosted stays 1."""
    db = SessionLocal()
    try:
        _tomato_task(db)
        obj = tmp_path / "iso_0.obj"
        trimesh.creation.box().export(str(obj))

        def fake_to_glb(path):
            return trimesh.load(path, force="mesh").export(file_type="glb")

        def boom_score(db_, out):
            raise RuntimeError("scorer unreachable")

        report = ingest_helios(
            db, [str(obj)], to_glb=fake_to_glb, score_fn=boom_score, variant="heliosIso"
        )
        assert report["hosted"] == 1
        assert report["errors"] == 0  # a scoring failure is not a provider error
        out = (
            db.execute(select(ModelOutput).where(ModelOutput.attribution.contains("heliosIso")))
            .scalars()
            .one()
        )
        assert out.asset_path  # hosted GLB survived the scoring rollback
    finally:
        db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_generate_helios.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.generate_helios'`

- [ ] **Step 3: Implement `scripts/generate_helios.py`**

```python
"""Generate procedural tomato plants with Helios (UC Davis) and ingest them as a
'procedural:helios' entry. `ingest_helios` is the testable core (OBJ->GLB + scorer injected);
`main()` runs the built ~/Helios/projects/tomato_gen binary, then collects + ingests. Commits
per object. Helios is a separate C++ build (NOT pip's unrelated `pyhelios` CFD package), invoked
by subprocess.
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
HELIOS_LICENSE = "GPL-2.0 (Helios, UC Davis Bailey Lab)"
HELIOS_URL = "https://github.com/PlantSimulationLab/Helios"


def ingest_helios(
    db, obj_paths, *, to_glb, score_fn=None, variant="tomato", task_title=TOMATO_TITLE, limit=10
) -> dict:
    task = db.execute(select(Task).where(Task.title == task_title)).scalars().first()
    if task is None:
        raise RuntimeError(f"subject task not found: {task_title!r}")
    report = {"hosted": 0, "skipped": 0, "errors": 0, "by_variant": {}}
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
                generator_slug="helios",
                generator_name="Helios",
                data=glb,
                ext="glb",
                title=asset_id,
                meta={"depiction": "whole_plant", "variant": variant, "render": "mesh"},
            )
            out.source = "procedural:helios"
            out.license = HELIOS_LICENSE
            out.attribution = f"Helios procedural {variant} (CanopyGenerator)"
            out.external_url = HELIOS_URL
            db.commit()  # provenance committed → hosted
            report["hosted"] += 1
            report["by_variant"][variant] = report["by_variant"].get(variant, 0) + 1
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
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                pass
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
    ap.add_argument(
        "--bin",
        default=os.environ.get("HELIOS_TOMATO_BIN", str(Path.home() / "Helios/projects/tomato_gen/build/tomato_gen")),
        help="path to the built Helios tomato_gen binary (or set HELIOS_TOMATO_BIN)",
    )
    ap.add_argument("-n", type=int, default=3, help="number of seeds/plants to generate")
    ap.add_argument("--max-faces", type=int, default=150_000)
    ap.add_argument("--no-score", action="store_true")
    args = ap.parse_args()

    if not Path(args.bin).exists():
        print(f"Helios tomato_gen binary not found: {args.bin} — build the project first")
        return 1
    out_dir = tempfile.mkdtemp(prefix="helios_")
    objs = []
    for seed in range(args.n):
        obj = str(Path(out_dir) / f"tomato_{seed}.obj")
        # tomato_gen writes an OBJ at argv[1] for the given seed (argv[2]); verify the binary's
        # arg contract against its main.cpp at build time.
        try:
            subprocess.run([args.bin, obj, str(seed)], check=True, timeout=600)
            if Path(obj).exists():
                objs.append(obj)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
            print(f"  helios seed {seed} failed: {e}")
    if not objs:
        print(f"no .obj produced under {out_dir}")
        return 1

    max_faces = args.max_faces or None

    def to_glb(path: str) -> bytes:
        return _to_glb(path, max_faces=max_faces)

    db = SessionLocal()
    try:
        report = ingest_helios(
            db, objs, to_glb=to_glb,
            score_fn=None if args.no_score else recon_service.score_and_store,
        )
        print(report)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_generate_helios.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Run the full suite + commit**

```bash
.venv/bin/python -m pytest -q   # expect: all pass
git add scripts/generate_helios.py tests/test_generate_helios.py
git commit -m "feat(helios): ingest adapter — procedural tomato as procedural:helios"
```

---

## Out of scope (operational, NOT a build task)

- **Build gate (controller, run FIRST):** clone Helios to `~/Helios`, write `~/Helios/projects/tomato_gen/`
  (`CMakeLists.txt` linking `core` + `canopygenerator`; `main.cpp` calling `CanopyGenerator` +
  `TomatoParameters` + `buildPlant()`/`buildCanopy()` + `context.writeOBJ(argv[1], true)` — verify the
  exact build API against `~/Helios/plugins/canopygenerator/include/CanopyGenerator.h`), build via CMake,
  generate one tomato OBJ, convert to GLB, and **independent-critic-eyeball** it. GO/NO-GO before the live ingest.
- **Live ingest (after the gate):** `HELIOS_TOMATO_BIN=<binary> .venv/bin/python scripts/generate_helios.py -n 3`,
  then render thumbnails + the independent-critic gate. Heavy build → submit via jobd if babysitting wanted.

## Notes for the implementer

- `mesh_convert.to_glb` already decimates above `max_faces` and raises `MeshConvertError` on a faces-less
  file — reuse it; do not write a new converter.
- The read-back tests filter on `attribution.contains(<unique variant>)`, NOT a bare
  `source=="procedural:helios"` `.one()` — the shared file-backed test DB accumulates rows across tests
  (this is the Critical lesson carried from the Infinigen increment; keep it).
- Never add Helios (or `pyhelios`) to `requirements.txt`; the PyPI `pyhelios` is an unrelated CFD package.
