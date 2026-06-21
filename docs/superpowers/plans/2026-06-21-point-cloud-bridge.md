# Point-cloud → POINTS-GLB Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render real scanned plants that ship as point clouds in the spotlight by exporting them as faithful glTF POINTS GLBs (no surface reconstruction), reusing the existing injected scan-ingest pipeline.

**Architecture:** A new `points_to_glb` converter (the mirror of `app/mesh_convert.to_glb`, which _rejects_ point clouds) loads a cloud, caps its size, preserves colours, and exports a glTF primitive-mode-0 (POINTS) GLB. The existing `ingest_scans` pipeline already takes its converter as an injected argument, so wiring is one new `render_kind` field plus a `--render points` entrypoint flag. The spotlight surfaces a "point cloud" badge.

**Tech Stack:** Python, trimesh (already a dep), numpy (already a dep), FastAPI/Jinja2, SQLAlchemy, model-viewer (renders glTF POINTS via its three.js core).

## Global Constraints

- Faithful points ONLY — export the cloud as glTF POINTS. NO surface reconstruction, NO new heavy dependency (no open3d). (Locked at brainstorm.)
- Do NOT modify the AgriGen scorer or anything under `/home/mjarnold/agrigen` (read-only, one-writer-per-repo).
- Scoring stays isolated/best-effort: a points GLB the scorer cannot handle stores `status=error`; the card still hosts. Never drop a hosted object on a scoring failure.
- Per-object commit in the ingest loop (short SQLite write-lock windows) — never hold the write lock across the scorer RPC.
- trimesh is the only mesh/cloud library; verified: `trimesh.PointCloud(verts, colors).export(file_type="glb")` writes primitive mode 0 (POINTS).
- `ingest_scans` current signature (live): `ingest_scans(db, mesh_paths, *, dataset, to_glb, score_fn=None, task_title=TOMATO_TITLE, limit=15)`. It writes `meta={"depiction": depiction, "dataset": dataset, "scan_id": scan_id}`.

---

## File Structure

- **Create** `app/points_convert.py` — `points_to_glb` + `PointsConvertError`. One responsibility: cloud → POINTS-GLB bytes. Mirror of `app/mesh_convert.py`.
- **Create** `tests/test_points_convert.py` — unit tests for the converter.
- **Modify** `scripts/source_scans.py` — add `render_kind` to `ingest_scans` (→ meta), add `--render {mesh,points}` + cloud globs + points-converter wiring to `main()`.
- **Modify** `tests/test_source_scans.py` — add a render_kind="points" ingest test.
- **Modify** `app/spotlight.py` (`build_spotlight`, ~line 103-124) — surface `render` from meta into the model dict.
- **Modify** `app/templates/spotlight.html` — "point cloud" badge on `render == "points"` cards.
- **Modify** `tests/test_spotlight_scan_group.py` — assert `render` on the dict + badge in the rendered page.

---

### Task 1: `points_to_glb` converter

**Files:**

- Create: `app/points_convert.py`
- Test: `tests/test_points_convert.py`

**Interfaces:**

- Consumes: nothing (trimesh, numpy only).
- Produces: `PointsConvertError(Exception)`; `points_to_glb(src_path: str, *, max_points: int = 200_000, seed: int = 0) -> bytes`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_points_convert.py
import json
import struct

import numpy as np
import pytest
import trimesh

from app.points_convert import PointsConvertError, points_to_glb


def _primitive_modes(glb: bytes) -> list[int]:
    # GLB: 12-byte header, then a JSON chunk (8-byte chunk header + payload).
    clen = struct.unpack("<I", glb[12:16])[0]
    j = json.loads(glb[20 : 20 + clen].decode("utf-8"))
    return [p.get("mode") for m in j.get("meshes", []) for p in m.get("primitives", [])]


def test_points_to_glb_exports_points_primitive(tmp_path):
    ply = tmp_path / "cloud.ply"
    trimesh.PointCloud(np.random.RandomState(0).rand(500, 3)).export(str(ply))
    glb = points_to_glb(str(ply))
    assert isinstance(glb, bytes) and glb[:4] == b"glTF"
    assert _primitive_modes(glb) == [0]  # 0 == POINTS


def test_points_to_glb_downsamples_above_cap(tmp_path):
    ply = tmp_path / "big.ply"
    trimesh.PointCloud(np.random.RandomState(1).rand(5000, 3)).export(str(ply))
    glb = points_to_glb(str(ply), max_points=1000, seed=0)
    rt = trimesh.load(trimesh.util.wrap_as_stream(glb), file_type="glb")
    pts = rt.vertices if hasattr(rt, "vertices") else rt.geometry[next(iter(rt.geometry))].vertices
    assert len(pts) == 1000


def test_points_to_glb_preserves_colors(tmp_path):
    verts = np.random.RandomState(2).rand(300, 3)
    colors = np.tile(np.array([[10, 200, 30, 255]], dtype=np.uint8), (300, 1))
    ply = tmp_path / "colored.ply"
    trimesh.PointCloud(verts, colors=colors).export(str(ply))
    glb = points_to_glb(str(ply))
    rt = trimesh.load(trimesh.util.wrap_as_stream(glb), file_type="glb")
    geom = rt if hasattr(rt, "colors") else rt.geometry[next(iter(rt.geometry))]
    # The dominant green channel must survive the round-trip.
    assert geom.colors is not None and len(geom.colors) > 0
    mean = np.asarray(geom.colors)[:, :3].mean(axis=0)
    assert mean[1] > mean[0] and mean[1] > mean[2]


def test_points_to_glb_raises_on_empty(tmp_path):
    empty = tmp_path / "empty.ply"
    trimesh.PointCloud(np.zeros((0, 3))).export(str(empty))
    with pytest.raises(PointsConvertError):
        points_to_glb(str(empty))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_points_convert.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.points_convert'`

- [ ] **Step 3: Write the implementation**

```python
# app/points_convert.py
"""Convert a scan-dataset point cloud (.ply/.pcd/.xyz, or a mesh used as points) to a
glTF POINTS GLB for <model-viewer>.

The mirror of app/mesh_convert.py, which REJECTS point clouds. Here we embrace them:
the points ARE the data, so we render them faithfully (no surface reconstruction).
trimesh.PointCloud exports primitive mode 0 (POINTS), which model-viewer's three.js
core renders. trimesh + numpy are already dependencies.
"""

from __future__ import annotations

import numpy as np
import trimesh


class PointsConvertError(Exception):
    """Raised when an asset has no usable vertices to render as a point cloud."""


def points_to_glb(src_path: str, *, max_points: int = 200_000, seed: int = 0) -> bytes:
    """Load a point-cloud asset and export a glTF POINTS GLB.

    Raises PointsConvertError if the asset has no vertices. A mesh source is accepted —
    its vertices become the point set (still faithful to the scan). Clouds larger than
    max_points are randomly subsampled (fixed seed → reproducible) so the GLB stays
    web-renderable; vertex colours, when present, are preserved through the subsample.
    """
    loaded = trimesh.load(src_path)  # NOT force="mesh" — keep the cloud
    verts = getattr(loaded, "vertices", None)
    if verts is None or len(verts) == 0:
        raise PointsConvertError(f"{src_path}: no vertices, nothing to render")
    verts = np.asarray(verts)

    colors = None
    if getattr(loaded, "colors", None) is not None and len(loaded.colors) == len(verts):
        colors = np.asarray(loaded.colors)
    else:
        visual = getattr(loaded, "visual", None)
        vc = getattr(visual, "vertex_colors", None) if visual is not None else None
        if vc is not None and len(vc) == len(verts):
            colors = np.asarray(vc)

    if len(verts) > max_points:
        idx = np.random.RandomState(seed).choice(len(verts), size=max_points, replace=False)
        verts = verts[idx]
        if colors is not None:
            colors = colors[idx]

    cloud = trimesh.PointCloud(verts, colors=colors)
    glb = cloud.export(file_type="glb")
    if not glb:
        raise PointsConvertError(f"{src_path}: empty GLB export")
    return glb
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_points_convert.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add app/points_convert.py tests/test_points_convert.py
git commit -m "feat(scan): points_to_glb — faithful point-cloud → POINTS-GLB bridge"
```

---

### Task 2: ingest pipeline — `render_kind` + `--render points`

**Files:**

- Modify: `scripts/source_scans.py` (`ingest_scans` signature + meta; `main()` arg + globs + converter)
- Test: `tests/test_source_scans.py`

**Interfaces:**

- Consumes: `app.points_convert.points_to_glb` (Task 1); existing `ingest_scans`.
- Produces: `ingest_scans(db, mesh_paths, *, dataset, to_glb, score_fn=None, task_title=TOMATO_TITLE, limit=15, render_kind="mesh")` — writes `"render": render_kind` into the output meta. `main()` gains `--render {mesh,points}`.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_source_scans.py
def test_ingest_scans_points_sets_render_meta(tmp_path):
    import json

    import numpy as np
    import trimesh
    from sqlalchemy import select

    from app.database import SessionLocal, init_db
    from app.models import ModelOutput
    from scripts.source_scans import ingest_scans

    init_db()
    db = SessionLocal()
    try:
        _tomato_task(db)  # existing helper in this test module
        ply = tmp_path / "c.ply"
        trimesh.PointCloud(np.random.RandomState(0).rand(200, 3)).export(str(ply))

        def fake_points_to_glb(path):
            return trimesh.PointCloud(np.random.RandomState(0).rand(200, 3)).export(file_type="glb")

        report = ingest_scans(
            db, [str(ply)], dataset="tomatowur", to_glb=fake_points_to_glb, render_kind="points"
        )
        assert report["hosted"] == 1
        out = db.execute(
            select(ModelOutput).where(ModelOutput.source == "tomatowur")
        ).scalars().one()
        assert json.loads(out.meta_json)["render"] == "points"
    finally:
        db.close()
```

(Note: confirm the existing `_tomato_task` helper sets the task title `ingest_scans` expects; if the module uses a different setup helper, reuse that one. Read `tests/test_source_scans.py` first.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_source_scans.py::test_ingest_scans_points_sets_render_meta -v`
Expected: FAIL — `ingest_scans() got an unexpected keyword argument 'render_kind'`

- [ ] **Step 3: Implement — add `render_kind` to `ingest_scans`**

In `scripts/source_scans.py`, change the signature and the meta dict:

```python
def ingest_scans(
    db, mesh_paths, *, dataset, to_glb, score_fn=None, task_title=TOMATO_TITLE,
    limit=15, render_kind="mesh",
) -> dict:
```

and in the `register_output(... meta=...)` call, change:

```python
                meta={"depiction": depiction, "dataset": dataset, "scan_id": scan_id},
```

to:

```python
                meta={
                    "depiction": depiction,
                    "dataset": dataset,
                    "scan_id": scan_id,
                    "render": render_kind,
                },
```

Then in `main()`, add the flag, cloud globs, and converter choice. Change the `main()` arg block to add:

```python
    ap.add_argument("--render", choices=("mesh", "points"), default="mesh")
```

After the existing `meshes = sorted(...)` glob, widen it so the points path also finds clouds:

```python
    exts = ("*.obj", "*.ply", "*.glb") if args.render == "mesh" else ("*.ply", "*.pcd", "*.xyz")
    meshes = sorted(str(p) for ext in exts for p in root.rglob(ext))
```

(Replace the existing `meshes = sorted(...)` line with the two lines above.)

Choose the converter and pass `render_kind`:

```python
    if args.render == "points":
        from app.points_convert import points_to_glb

        def to_glb(path: str) -> bytes:
            return points_to_glb(path)
    else:
        max_faces = args.max_faces or None

        def to_glb(path: str) -> bytes:
            return _to_glb(path, max_faces=max_faces)
```

(This replaces the existing `max_faces`/`def to_glb` block. Keep the existing `from app.mesh_convert import to_glb as _to_glb` import.)

And pass it through to `ingest_scans`:

```python
        report = ingest_scans(
            db,
            meshes,
            dataset=args.dataset,
            to_glb=to_glb,
            score_fn=None if args.no_score else recon_service.score_and_store,
            limit=args.limit,
            render_kind=args.render,
        )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_source_scans.py -v`
Expected: PASS (all source_scans tests, including the new one)

- [ ] **Step 5: Commit**

```bash
git add scripts/source_scans.py tests/test_source_scans.py
git commit -m "feat(scan): --render points wires the points bridge into ingest_scans"
```

---

### Task 3: spotlight — surface `render` + "point cloud" badge

**Files:**

- Modify: `app/spotlight.py` (`build_spotlight` model dict)
- Modify: `app/templates/spotlight.html` (badge)
- Test: `tests/test_spotlight_scan_group.py`

**Interfaces:**

- Consumes: meta `"render"` from Task 2.
- Produces: model dict key `"render"` (str, defaults `"mesh"`); a `point cloud` badge in the rendered card when `render == "points"`.

- [ ] **Step 1: Write the failing test**

Append this self-contained test to `tests/test_spotlight_scan_group.py` (the file already
imports `trimesh`, `TestClient`, `app`, `ingest`, `spotlight`, `SessionLocal`, `Category`, `Task`).
It asserts both the dict field (`render == "points"`) and the rendered badge:

```python
def test_points_scan_card_renders_point_cloud_badge(monkeypatch):
    db = SessionLocal()
    try:
        cat = db.query(Category).filter_by(slug="plants").first() or Category(
            slug="plants", name="Plants"
        )
        db.add(cat)
        db.flush()
        task = Task(category_id=cat.id, title="PC Badge Subject", prompt="p")
        db.add(task)
        db.flush()
        glb = trimesh.PointCloud([[0, 0, 0], [1, 1, 1], [0, 1, 0]]).export(file_type="glb")
        out, _ = ingest.register_output(
            db,
            task_id=task.id,
            generator_slug="scan:tomatowur",
            generator_name="TomatoWUR",
            data=glb,
            ext="glb",
            title="pcBadgeA",
            meta={"depiction": "whole_plant", "dataset": "tomatowur", "render": "points"},
        )
        out.source = "tomatowur"
        db.commit()
        model = spotlight.build_spotlight(db, "pcbadge")["models"][0]
        assert model["render"] == "points"
    finally:
        db.close()

    monkeypatch.setattr(
        spotlight,
        "SPOTLIGHTS",
        [{"slug": "pcbadge", "task_title": "PC Badge Subject", "featured": True,
          "order": 0, "blurb": "b", "reference_image": None}],
    )
    page = TestClient(app).get("/spotlight/pcbadge")
    assert page.status_code == 200
    assert "point cloud" in page.text
```

(The file already imports `trimesh`, `TestClient`, `app`, `ingest`, `spotlight`, `SessionLocal`, `Category`, `Task`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest "tests/test_spotlight_scan_group.py::test_points_scan_card_renders_point_cloud_badge" -v`
Expected: FAIL — `KeyError: 'render'` (model dict has no `render` yet)

- [ ] **Step 3: Implement — surface `render` + badge**

In `app/spotlight.py`, in `build_spotlight`'s per-output loop, after `dataset = meta.get("dataset")` add:

```python
        render = meta.get("render", "mesh")
```

and add to the `models.append({...})` dict (next to `"dataset": dataset,`):

```python
                "render": render,
```

In `app/templates/spotlight.html`, in the card body, just after the `card-gen` div (the `<div class="card-gen">…</div>` line), add:

```html
{% if m.render == 'points' %}
<div class="card-badge">point cloud</div>
{% endif %}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_spotlight_scan_group.py -v`
Expected: PASS (all scan-group tests)

- [ ] **Step 5: Run the full suite + commit**

```bash
.venv/bin/python -m pytest -q   # expect: all pass
git add app/spotlight.py app/templates/spotlight.html tests/test_spotlight_scan_group.py
git commit -m "feat(spotlight): point-cloud render badge for POINTS-GLB scan cards"
```

---

## Out of scope (do NOT build here)

- Live ingest of a real point-cloud dataset (data-gated — needs TomatoWUR/Crops3D/Pheno4D downloaded; the mesh path's live ingest was the same deferred pattern). Code ships synthetic-tested; the operational ingest + render + independent-critic gate run when a real cloud is in hand.
- Surface reconstruction "derived surface" cards (rejected approach).
- model-viewer point-size custom material — only if the render+critic gate on real data shows points are illegible.

## Notes for the implementer

- The render+critic gate (the real-execution check) is NOT a code task here; it runs after a live cloud dataset is downloaded, exactly as it did for the mesh path (independent critic FAILED v1 white-on-white, fixed with gray bg — the points path will get the same scrutiny on real data).
- A points GLB the AgriGen scorer can't chamfer is fine: scoring is isolated/best-effort and the card hosts regardless. Do NOT try to make the scorer handle points (AgriGen is read-only).
