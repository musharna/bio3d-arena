# Real-Scan Dataset Ingest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ingest real scanned whole tomato plants from academic datasets onto the tomato spotlight so the grid shows AI-recon vs real-scan vs artist-found side by side.

**Architecture:** Pure source-class + dataset registry in `app/sourcing.py`; a trimesh mesh→GLB bridge in `app/mesh_convert.py`; an injectable ingest pipeline in `scripts/source_scans.py`; `build_spotlight` + template group by a three-way source class. The dataset code is built/tested with synthetic fixtures; a final operational task probes/downloads a real dataset (Plant3D-first, fallback TomatoWUR/Crops3D) and ingests it.

**Tech Stack:** FastAPI + SQLAlchemy 2.0 (SQLite), Jinja2, trimesh (already installed, 4.12.2), pytest.

## Global Constraints

- Python 3.13. Run tests `.venv/bin/python -m pytest`; lint `ruff check app/ tests/ scripts/`.
- Scan models reuse `ModelOutput` (asset_path NOT NULL — host the converted GLB). Provenance set AFTER `register_output` (which sets neither provenance nor commits); commit PER object.
- A single generator per dataset: `generator_slug="scan:<dataset>"`, `generator_name=<dataset display name>`. Card label = the output title (the scan id).
- Only `depiction=="whole_plant"` is scored; a scoring failure must NOT drop the hosted object (isolated try, same as the Objaverse pipeline).
- License policy unchanged (host CC/public-domain incl. NC/ND; record exact license). `/spotlight` stays internal.
- `<model-viewer>` renders GLB only — every scan mesh is converted to GLB; point-cloud assets (no faces) are skipped and counted, not silently dropped.

---

### Task 1: Source class + scan-dataset registry

**Files:**

- Modify: `app/sourcing.py`
- Test: `tests/test_source_class.py`

**Interfaces:**

- Produces: `source_class(source: str | None) -> "ai" | "scan" | "found"`; `SCAN_DATASETS: dict[str, dict]` (slug → `{name, license, attribution, url}`); `SCAN_SOURCES: frozenset[str]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_source_class.py
from app.sourcing import SCAN_DATASETS, source_class


def test_source_class_buckets():
    assert source_class("bio3d-arena") == "ai"
    assert source_class("plant3d") == "scan"
    assert source_class("tomatowur") == "scan"
    assert source_class("objaverse") == "found"
    assert source_class("sketchfab") == "found"
    assert source_class(None) == "found"


def test_scan_registry_has_required_fields():
    for slug, meta in SCAN_DATASETS.items():
        assert {"name", "license", "attribution", "url"} <= set(meta), slug
        assert source_class(slug) == "scan"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_source_class.py -v`
Expected: FAIL — `ImportError: cannot import name 'source_class'`.

- [ ] **Step 3: Implement (append to `app/sourcing.py`)**

```python
# --- Source classes for the spotlight grid (ai recon / real scan / artist-found) ---
SCAN_DATASETS: dict[str, dict] = {
    "plant3d": {
        "name": "Plant3D (Salk)",
        "license": "CC-BY 4.0",
        "attribution": "Salk Institute / Navlakha lab — Plant3D, Mendeley 10.17632/9k7zctdyhs.1",
        "url": "https://data.mendeley.com/datasets/9k7zctdyhs/1",
    },
    "tomatowur": {
        "name": "TomatoWUR",
        "license": "CC-BY 4.0",
        "attribution": "Wageningen University & Research — TomatoWUR (4TU.ResearchData)",
        "url": "https://data.4tu.nl/",
    },
    "crops3d": {
        "name": "Crops3D",
        "license": "CC-BY-NC-ND 4.0",
        "attribution": "Crops3D (Nature Scientific Data 2024)",
        "url": "https://doi.org/10.1038/s41597-024-04290-0",
    },
}
SCAN_SOURCES = frozenset(SCAN_DATASETS)


def source_class(source: str | None) -> str:
    """Group key for the spotlight: 'ai' (our recon), 'scan' (real scan dataset),
    'found' (artist repos like Objaverse/Sketchfab)."""
    if source == "bio3d-arena":
        return "ai"
    if source in SCAN_SOURCES:
        return "scan"
    return "found"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_source_class.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/sourcing.py tests/test_source_class.py
git commit -m "feat(scan): source_class + scan-dataset registry"
```

---

### Task 2: Mesh → GLB bridge

**Files:**

- Create: `app/mesh_convert.py`
- Test: `tests/test_mesh_convert.py`

**Interfaces:**

- Produces: `to_glb(src_path: str) -> bytes` (GLB bytes for a faced mesh); `class MeshConvertError(Exception)` (raised for point-cloud / no-face inputs).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mesh_convert.py
import numpy as np
import pytest
import trimesh

from app.mesh_convert import MeshConvertError, to_glb


def test_to_glb_converts_a_mesh(tmp_path):
    obj = tmp_path / "box.obj"
    trimesh.creation.box().export(str(obj))
    glb = to_glb(str(obj))
    assert isinstance(glb, bytes) and glb[:4] == b"glTF"
    # round-trips back to a faced mesh
    loaded = trimesh.load(trimesh.util.wrap_as_stream(glb), file_type="glb", force="mesh")
    assert len(loaded.faces) > 0


def test_to_glb_rejects_point_cloud(tmp_path):
    ply = tmp_path / "cloud.ply"
    trimesh.PointCloud(np.random.rand(64, 3)).export(str(ply))
    with pytest.raises(MeshConvertError):
        to_glb(str(ply))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_mesh_convert.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.mesh_convert'`.

- [ ] **Step 3: Implement**

```python
# app/mesh_convert.py
"""Convert a scan-dataset mesh (.obj/.ply/.glb) to GLB bytes for <model-viewer>.

Point-cloud assets (vertices, no faces) cannot be rendered as a surface mesh by
model-viewer, so they raise MeshConvertError and are skipped by callers (a future
increment can add a cloud→points-GLTF bridge). trimesh is already a dependency.
"""

from __future__ import annotations

import trimesh


class MeshConvertError(Exception):
    """Raised when an asset cannot be converted to a renderable GLB mesh."""


def to_glb(src_path: str) -> bytes:
    loaded = trimesh.load(src_path, force="mesh")  # concatenate scene parts into one mesh
    faces = getattr(loaded, "faces", None)
    if faces is None or len(faces) == 0:
        raise MeshConvertError(f"{src_path}: point-cloud / no faces, not renderable")
    glb = loaded.export(file_type="glb")
    if not glb:
        raise MeshConvertError(f"{src_path}: empty GLB export")
    return glb
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_mesh_convert.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add app/mesh_convert.py tests/test_mesh_convert.py
git commit -m "feat(scan): trimesh mesh→GLB bridge (skips point clouds)"
```

---

### Task 3: Scan ingest pipeline

**Files:**

- Create: `scripts/source_scans.py`
- Test: `tests/test_source_scans.py`

**Interfaces:**

- Consumes: `app.ingest.register_output`; `app.sourcing.SCAN_DATASETS`; `app.mesh_convert.to_glb` (injectable); the tomato Task.
- Produces: `ingest_scans(db, mesh_paths, *, dataset, to_glb, score_fn=None, task_title=TOMATO_TITLE, limit=15) -> dict` returning `{"hosted","skipped_pointcloud","errors","by_depiction"}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_source_scans.py
import json

import numpy as np
import trimesh

from app.database import SessionLocal, init_db
from app.mesh_convert import MeshConvertError, to_glb
from app.models import Category, ModelOutput, Task
from scripts.source_scans import ingest_scans

TOMATO = "Solanum lycopersicum — single-image → 3D reconstruction"


def setup_module(_m):
    init_db()


def _tomato_task(db):
    cat = db.query(Category).filter_by(slug="plants").first() or Category(slug="plants", name="Plants")
    db.add(cat)
    db.flush()
    t = Task(category_id=cat.id, title=TOMATO, prompt="p")
    db.add(t)
    db.commit()
    return t


def test_ingest_scans_hosts_mesh_skips_cloud(tmp_path):
    db = SessionLocal()
    try:
        _tomato_task(db)
        mesh = tmp_path / "scan1.obj"
        trimesh.creation.box().export(str(mesh))
        cloud = tmp_path / "scan2.ply"
        trimesh.PointCloud(np.random.rand(40, 3)).export(str(cloud))
        report = ingest_scans(
            db, [str(mesh), str(cloud)], dataset="plant3d", to_glb=to_glb, score_fn=None,
        )
        assert report["hosted"] == 1
        assert report["skipped_pointcloud"] == 1
        out = db.query(ModelOutput).filter(ModelOutput.source == "plant3d").one()
        assert out.license == "CC-BY 4.0"
        assert out.asset_format == "glb"
        assert "Salk" in (out.attribution or "")
        assert json.loads(out.meta_json)["depiction"] == "whole_plant"
        assert json.loads(out.meta_json)["dataset"] == "plant3d"
    finally:
        db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_source_scans.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.source_scans'`.

- [ ] **Step 3: Implement**

```python
# scripts/source_scans.py
"""Ingest real scanned whole-plant meshes from an academic dataset onto the tomato
spotlight Task. `ingest_scans` is the testable core (mesh→GLB + scorer injected);
`main()` wires a dataset adapter (local mesh glob) + the recon scorer. Commits per object.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app import ingest  # noqa: E402
from app.mesh_convert import MeshConvertError  # noqa: E402
from app.models import Task  # noqa: E402
from app.sourcing import SCAN_DATASETS  # noqa: E402

TOMATO_TITLE = "Solanum lycopersicum — single-image → 3D reconstruction"


def ingest_scans(db, mesh_paths, *, dataset, to_glb, score_fn=None,
                 task_title=TOMATO_TITLE, limit=15) -> dict:
    meta_d = SCAN_DATASETS[dataset]
    task = db.execute(select(Task).where(Task.title == task_title)).scalars().first()
    if task is None:
        raise RuntimeError(f"subject task not found: {task_title!r}")
    report = {"hosted": 0, "skipped_pointcloud": 0, "errors": 0, "by_depiction": {}}
    for path in list(mesh_paths)[:limit]:
        scan_id = Path(path).stem
        try:
            try:
                glb = to_glb(path)
            except MeshConvertError as e:
                print(f"  skip (point-cloud) {scan_id}: {e}")
                report["skipped_pointcloud"] += 1
                continue
            depiction = "whole_plant"
            out, _created = ingest.register_output(
                db, task_id=task.id, generator_slug=f"scan:{dataset}",
                generator_name=meta_d["name"], data=glb, ext="glb", title=scan_id,
                meta={"depiction": depiction, "dataset": dataset, "scan_id": scan_id},
            )
            out.source = dataset
            out.license = meta_d["license"]
            out.attribution = meta_d["attribution"]
            out.external_url = meta_d["url"]
            db.commit()  # provenance committed → hosted
            report["hosted"] += 1
            report["by_depiction"][depiction] = report["by_depiction"].get(depiction, 0) + 1
            if score_fn is not None and depiction == "whole_plant":
                try:
                    score_fn(db, out)
                    db.commit()
                except Exception as e:  # noqa: BLE001 — scoring best-effort; object stays hosted
                    print(f"  score failed for {out.id}: {e}")
                    db.rollback()
        except Exception as e:  # noqa: BLE001 — one bad mesh never aborts the batch
            print(f"  error {scan_id}: {e}")
            report["errors"] += 1
            db.rollback()
    return report


def main() -> int:
    import argparse

    from app import recon_service
    from app.database import SessionLocal
    from app.mesh_convert import to_glb

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", required=True, choices=sorted(SCAN_DATASETS))
    ap.add_argument("--dir", required=True, help="local dir containing the tomato mesh files")
    ap.add_argument("--limit", type=int, default=15)
    ap.add_argument("--no-score", action="store_true")
    args = ap.parse_args()

    root = Path(args.dir)
    if not root.exists():
        print(f"dataset dir not found: {root} — download the dataset first")
        return 1
    meshes = sorted(str(p) for ext in ("*.obj", "*.ply", "*.glb") for p in root.rglob(ext))
    if not meshes:
        print(f"no .obj/.ply/.glb meshes under {root}")
        return 1
    db = SessionLocal()
    try:
        report = ingest_scans(
            db, meshes, dataset=args.dataset, to_glb=to_glb,
            score_fn=None if args.no_score else recon_service.score_and_store,
            limit=args.limit,
        )
        print(report)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_source_scans.py -v`
Expected: PASS.

- [ ] **Step 5: Run full suite + lint**

Run: `.venv/bin/python -m pytest -q && ruff check app/ tests/ scripts/`
Expected: all pass, ruff clean.

- [ ] **Step 6: Commit**

```bash
git add scripts/source_scans.py tests/test_source_scans.py
git commit -m "feat(scan): scan-dataset ingest pipeline (mesh→GLB, provenance, isolated scoring)"
```

---

### Task 4: Three-way grid grouping (AI / scan / found)

**Files:**

- Modify: `app/spotlight.py` (the `build_spotlight` model dict)
- Modify: `app/templates/spotlight.html` (group by source class)
- Test: `tests/test_spotlight_scan_group.py`

**Interfaces:**

- Consumes: `app.sourcing.source_class`; `build_spotlight`.
- Produces: each model dict gains `cls` (`"ai"|"scan"|"found"`) and `dataset` (from meta, or None).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_spotlight_scan_group.py
import trimesh

from app import ingest, spotlight
from app.database import SessionLocal, init_db
from app.models import Category, Task


def setup_module(_m):
    init_db()


def test_build_spotlight_marks_scan_class(tmp_path, monkeypatch):
    db = SessionLocal()
    try:
        cat = db.query(Category).filter_by(slug="plants").first() or Category(slug="plants", name="Plants")
        db.add(cat)
        db.flush()
        task = Task(category_id=cat.id, title="Scan Subject", prompt="p")
        db.add(task)
        db.flush()
        glb = tmp_path / "s.glb"
        trimesh.creation.box().export(str(glb))
        out, _ = ingest.register_output(
            db, task_id=task.id, generator_slug="scan:plant3d", generator_name="Plant3D (Salk)",
            data=glb.read_bytes(), ext="glb", title="scanA",
            meta={"depiction": "whole_plant", "dataset": "plant3d"},
        )
        out.source = "plant3d"
        db.commit()
        monkeypatch.setattr(spotlight, "SPOTLIGHTS", [
            {"slug": "s", "task_title": "Scan Subject", "featured": True, "order": 0,
             "blurb": "b", "reference_image": None},
        ])
        m = spotlight.build_spotlight(db, "s")["models"][0]
        assert m["cls"] == "scan"
        assert m["dataset"] == "plant3d"
        assert m["label"] == "scanA"
    finally:
        db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_spotlight_scan_group.py -v`
Expected: FAIL — `KeyError: 'cls'`.

- [ ] **Step 3: Extend `build_spotlight`'s model dict (`app/spotlight.py`)**

Add the import at the top of `app/spotlight.py` (with the other imports):

```python
from .sourcing import source_class
```

Inside the `for o in outs:` loop, where `found`/`label`/`depiction` are computed, add:

```python
        cls = source_class(o.source)
        dataset = json.loads(o.meta_json or "{}").get("dataset")
```

and add to the appended model dict (alongside the existing keys):

```python
                "cls": cls,
                "dataset": dataset,
```

(Keep the existing `found`, `label`, `depiction` keys unchanged.)

- [ ] **Step 4: Group the template (`app/templates/spotlight.html`)**

Replace the existing `{% set groups = [...] %}` block (the AI/Found grouping) with a three-way version:

```html
{% set ai = s.models | selectattr('cls', 'equalto', 'ai') | list %} {% set scan
= s.models | selectattr('cls', 'equalto', 'scan') | list %} {% set found =
s.models | selectattr('cls', 'equalto', 'found') | list %} {% set groups = [
('AI reconstruction', ai), ('Real scan — whole plant', scan |
selectattr('depiction', 'equalto', 'whole_plant') | list), ('Real scan — other',
scan | rejectattr('depiction', 'equalto', 'whole_plant') | list), ('Found —
whole plant', found | selectattr('depiction', 'equalto', 'whole_plant') | list),
('Found — fruit', found | selectattr('depiction', 'equalto', 'fruit') | list),
('Found — leaf', found | selectattr('depiction', 'equalto', 'leaf') | list),
('Found — other', found | selectattr('depiction', 'equalto', 'other') | list), ]
%}
```

(The `{% for gname, gmodels in groups %}{% if gmodels %}…` rendering loop below it is unchanged — empty groups still render nothing.)

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/test_spotlight_scan_group.py tests/test_spotlight_found.py tests/test_spotlight_page.py -v`
Expected: PASS (the existing found + page tests still pass — found models keep `cls=="found"`).

- [ ] **Step 6: Commit**

```bash
git add app/spotlight.py app/templates/spotlight.html tests/test_spotlight_scan_group.py
git commit -m "feat(spotlight): three-way grid grouping (AI recon / real scan / found)"
```

---

### Task 5: Acquire a real dataset, ingest, verify (operational)

**Files:** none (operational; controller-run). Records the chosen dataset in the SDD ledger.

- [ ] **Step 1: Probe Plant3D (content + acquisition gate)**

Fetch the Mendeley dataset listing for DOI 10.17632/9k7zctdyhs.1 (the dataset's file manifest / sizes) and confirm: (a) it is downloadable without auth, (b) the tomato subset contains whole-plant `.obj` meshes with faces (NOT just `.ply` point clouds or fruit/organ scans), (c) total size is manageable to fetch a ~15-mesh sample. If any check fails, switch to **TomatoWUR** (4TU.ResearchData, CC-BY) or **Crops3D** (figshare/HF, CC-BY-NC-ND) — whichever a quick probe confirms has renderable whole-tomato meshes. **Record the chosen dataset + why in the ledger.** Use a `signal.alarm` wall-guard on any inline fetch script.

- [ ] **Step 2: Download a sample into the runtime data dir**

Download the tomato meshes for the chosen dataset into `data/scans/<dataset>/` (gitignored under `/data/`). Fetch only what's needed for a ~15-mesh sample (don't pull the whole multi-GB archive if a subset suffices).

- [ ] **Step 3: Ingest onto the live DB**

Run: `BIO3D_DATABASE_URL="sqlite:///data/arena.db" BIO3D_RECON_SCORER_URL="http://127.0.0.1:8077" .venv/bin/python scripts/source_scans.py --dataset <chosen> --dir data/scans/<chosen> --limit 15`
Expected: report with `hosted >= 1`; note `skipped_pointcloud` if any.

- [ ] **Step 4: Render thumbnails**

Run: `BIO3D_DATABASE_URL="sqlite:///data/arena.db" .venv/bin/python scripts/render_spotlight.py --slug tomato`
Expected: `errors: 0`.

- [ ] **Step 5: Verify the page + independent-critic gate**

Restart the dev server; confirm `GET /spotlight/tomato` is 200 and now shows a **"Real scan — whole plant"** group beside "AI reconstruction" and "Found — fruit". Screenshot it and run a fresh independent adversarial critic (per the independent-critic doctrine) — verifying the scan thumbnails show real plant geometry and the three classes are distinguishable — before declaring done.

- [ ] **Step 6: Full suite + lint + commit**

Run: `.venv/bin/python -m pytest -q && ruff check app/ tests/ scripts/`. Commit any operational artifacts/fixes: `git add -A && git commit -m "chore(scan): live <dataset> ingest + operational verification"`.

---

## Self-Review

**Spec coverage:** source_class + registry (Task 1) ✓; mesh→GLB bridge incl. point-cloud skip (Task 2) ✓; ingest pipeline with per-object commit + isolated scoring + provenance + cap (Task 3) ✓; three-way grouping (Task 4) ✓; acquisition/content gate with Plant3D-first + fallback, real ingest, render, page verify, critic gate (Task 5) ✓; whole-plant-only scoring (Task 3 score_fn gate) ✓; real-execution check (Task 5 ingests real meshes; Tasks 2–3 use real trimesh meshes as fixtures, not mocks) ✓. Point clouds / Infinigen / direct repos out of scope (spec) — not planned ✓.

**Placeholder scan:** no "TBD"/"handle errors"/"similar to" — every code step carries complete code; Task 4's template step shows the exact Jinja and the exact dict-key additions; the rendering loop is explicitly "unchanged".

**Type consistency:** `source_class(source) -> str` used in Tasks 1 & 4. `to_glb(src_path) -> bytes` / `MeshConvertError` used in Tasks 2 & 3. `ingest_scans(db, mesh_paths, *, dataset, to_glb, score_fn, task_title, limit)` matches its test and `main()`. `register_output(..., generator_slug, generator_name, data, ext, title, meta)` matches `app/ingest.py`. `build_spotlight` model dict gains `cls`/`dataset`, consumed by the template. `score_fn(db, out)` matches `recon_service.score_and_store`.
