# Volumetric (CT/MRI) Modality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a volumetric (CT/MRI) sensor modality to the arena — a real imaging volume → marching-cubes surface mesh → GLB — piloted on open barley root MRI (cereal stand-in for the verified maize-volumetric gap).

**Architecture:** A pure conversion module (`volume_convert.volume_to_glb`) reads a NIfTI/TIFF volume, thresholds it, runs marching cubes, decimates, exports GLB. A thin ingest script (`source_volumetric.py`, mirroring `source_scans.py`) hosts the result with provenance. A new `volumetric` source class and a new barley-MRI spotlight subject surface it as a distinct sensor axis.

**Tech Stack:** Python, SQLAlchemy, trimesh, numpy, scikit-image (marching cubes + Otsu), scipy, nibabel (NIfTI), tifffile (TIFF stacks). Tests: pytest.

## Global Constraints

- **Work in the worktree:** `/home/mjarnold/bio3d-arena/.claude/worktrees/bio3d-arena-mvp/`. The populated DB + assets live ONLY there.
- **Interpreter:** the worktree venv — `.venv/bin/python` (NOT miniconda base). Run tests with `.venv/bin/python -m pytest`.
- **Commits:** on branch `worktree-bio3d-arena-mvp`. End every commit message with these two lines verbatim:
  ```
  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01Su8b5Eq8Xjb35JUmHbxnG9
  ```
- **Test isolation (shared session DB):** the test DB persists across modules (`conftest.py` temp dir, `init_db` = create-all only, no reset). So: **get-or-create** the subject Task (never blindly `db.add` a duplicate-title Task), and scope `ModelOutput` assertions by `(source, task_id)` — **never** a bare `.one()` on `Task.title`.
- **License policy:** the barley MRI is CC-BY-4.0 (public-safe). Record license/attribution/url on every output.
- **Subject task title (exact, used as a lookup key):** `Hordeum vulgare — barley root system (3D MRI)`.
- **Source string format:** `mri:ipk-barley-mri` (general form `<modality>:<dataset>`, modality lowercased).

---

### Task 1: Add volumetric conversion dependencies

**Files:**

- Modify: `requirements.txt`

**Interfaces:**

- Produces: importable `skimage`, `scipy`, `nibabel`, `tifffile` in the worktree venv (consumed by Task 3).

- [ ] **Step 1: Append the dependencies to `requirements.txt`**

Add these lines (exact):

```
scikit-image>=0.24
scipy>=1.13
nibabel>=5.2
tifffile>=2024.1.30
```

- [ ] **Step 2: Install into the worktree venv**

Run: `.venv/bin/pip install scikit-image scipy nibabel tifffile`
Expected: installs succeed (manylinux wheels).

- [ ] **Step 3: Verify imports**

Run: `.venv/bin/python -c "import skimage, scipy, nibabel, tifffile; from skimage import measure, filters; print('ok')"`
Expected: prints `ok`

- [ ] **Step 4: Commit**

```bash
git add requirements.txt
git commit -m "build(volumetric): add scikit-image/scipy/nibabel/tifffile for CT/MRI conversion

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Su8b5Eq8Xjb35JUmHbxnG9"
```

---

### Task 2: `volumetric` source class + dataset registry

**Files:**

- Modify: `app/sourcing.py` (add `VOLUMETRIC_DATASETS` after `SCAN_DATASETS`; add a branch to `source_class`)
- Test: `tests/test_sourcing.py`

**Interfaces:**

- Produces: `app.sourcing.VOLUMETRIC_DATASETS` (dict keyed by dataset slug → `{name,license,attribution,url,modality}`); `source_class("mri:x")` / `source_class("ct:x")` → `"volumetric"`. Consumed by Tasks 4 and 6.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_sourcing.py` (create the file if it does not exist; if it exists, append these tests):

```python
from app.sourcing import VOLUMETRIC_DATASETS, source_class


def test_source_class_volumetric():
    assert source_class("mri:ipk-barley-mri") == "volumetric"
    assert source_class("ct:some-dataset") == "volumetric"
    # existing classes still resolve
    assert source_class("crops3d") == "scan"
    assert source_class("bio3d-arena") == "ai"
    assert source_class("procedural:lpy") == "procedural"


def test_volumetric_dataset_registry_barley():
    d = VOLUMETRIC_DATASETS["ipk-barley-mri"]
    assert d["license"] == "CC-BY 4.0"
    assert d["modality"] == "MRI"
    assert "10.5447/IPK/2017/10" in d["url"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_sourcing.py -q`
Expected: FAIL (ImportError: cannot import name `VOLUMETRIC_DATASETS`)

- [ ] **Step 3: Add the registry + source-class branch**

In `app/sourcing.py`, after the `SCAN_SOURCES = frozenset(SCAN_DATASETS)` line, add:

```python
# Volumetric / tomographic datasets (CT / MRI / X-ray). A real measured-3D sensor axis distinct
# from the LiDAR/photogrammetry `scan` class. Source strings are `<modality>:<dataset>`.
VOLUMETRIC_DATASETS: dict[str, dict] = {
    "ipk-barley-mri": {
        "name": "IPK barley root MRI",
        "license": "CC-BY 4.0",
        "attribution": "3D MRI of three-week-old barley roots — IPK Gatersleben e!DAL-PGP "
        "(Pflugfelder et al.; DOI 10.5447/IPK/2017/10)",
        "url": "https://doi.org/10.5447/IPK/2017/10",
        "modality": "MRI",
    },
}
```

In `source_class`, add a branch BEFORE the `if source in SCAN_SOURCES:` line:

```python
    if (source or "").startswith(("ct:", "mri:")):
        return "volumetric"  # CT/MRI/X-ray tomography — a real-measured-3D sensor axis
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_sourcing.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/sourcing.py tests/test_sourcing.py
git commit -m "feat(volumetric): add 'volumetric' source class + barley-MRI dataset registry

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Su8b5Eq8Xjb35JUmHbxnG9"
```

---

### Task 3: `volume_convert.volume_to_glb`

**Files:**

- Create: `app/volume_convert.py`
- Test: `tests/test_volume_convert.py`

**Interfaces:**

- Produces: `volume_to_glb(src_path: str, *, threshold: float | None = None, max_faces: int = 200_000, step_size: int = 1) -> bytes` and `class VolumeConvertError(Exception)`. Consumed by Task 6 (`main()` injects it as `to_glb`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_volume_convert.py`:

```python
import io

import numpy as np
import pytest
import tifffile
import trimesh

from app.volume_convert import VolumeConvertError, volume_to_glb


def _sphere(n=40, r=12):
    """A solid sphere occupancy volume (uint8 0/255) — a clean marching-cubes target."""
    zz, yy, xx = np.mgrid[:n, :n, :n]
    c = n // 2
    vol = ((xx - c) ** 2 + (yy - c) ** 2 + (zz - c) ** 2 < r * r).astype(np.uint8) * 255
    return vol


def _mesh_from_glb(glb: bytes):
    return trimesh.load(io.BytesIO(glb), file_type="glb", force="mesh")


def test_volume_to_glb_tiff_stack(tmp_path):
    p = tmp_path / "sphere.tif"
    tifffile.imwrite(str(p), _sphere())  # 3-D array → multipage TIFF
    glb = volume_to_glb(str(p))
    m = _mesh_from_glb(glb)
    assert len(m.faces) > 0


def test_volume_to_glb_nifti(tmp_path):
    nib = pytest.importorskip("nibabel")
    p = tmp_path / "sphere.nii"
    nib.save(nib.Nifti1Image(_sphere().astype(np.float32), affine=np.eye(4)), str(p))
    glb = volume_to_glb(str(p))
    assert len(_mesh_from_glb(glb).faces) > 0


def test_volume_to_glb_explicit_threshold(tmp_path):
    p = tmp_path / "sphere.tif"
    tifffile.imwrite(str(p), _sphere())
    glb = volume_to_glb(str(p), threshold=128.0)
    assert len(_mesh_from_glb(glb).faces) > 0


def test_volume_to_glb_decimates(tmp_path):
    p = tmp_path / "sphere.tif"
    tifffile.imwrite(str(p), _sphere(n=64, r=22))
    glb = volume_to_glb(str(p), max_faces=2000)
    assert 0 < len(_mesh_from_glb(glb).faces) <= 2200  # decimated near the budget


def test_volume_to_glb_empty_raises(tmp_path):
    p = tmp_path / "empty.tif"
    tifffile.imwrite(str(p), np.zeros((20, 20, 20), dtype=np.uint8))  # no contrast → no surface
    with pytest.raises(VolumeConvertError):
        volume_to_glb(str(p))


def test_volume_to_glb_unsupported_format(tmp_path):
    p = tmp_path / "x.xyz"
    p.write_text("not a volume")
    with pytest.raises(VolumeConvertError):
        volume_to_glb(str(p))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_volume_convert.py -q`
Expected: FAIL (ModuleNotFoundError: No module named 'app.volume_convert')

- [ ] **Step 3: Write the implementation**

Create `app/volume_convert.py`:

```python
"""Convert a volumetric scan (CT / micro-CT / X-ray / MRI) to a surface-mesh GLB for
<model-viewer>. The volumetric sibling of mesh_convert.py (meshes) and points_convert.py
(point clouds): here the data is a 3-D intensity volume, so we threshold it to an occupancy
field and extract an iso-surface with marching cubes. trimesh + numpy were already deps;
scikit-image / nibabel / tifffile are added for this modality.

Reads a NIfTI (.nii/.nii.gz; voxel spacing from the affine) or a TIFF z-stack (.tif/.tiff,
a single multipage file OR a directory of slices; isotropic unit spacing). Other formats raise
VolumeConvertError — add a reader when a dataset needs it (DICOM/RAW are deliberately deferred).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import trimesh


class VolumeConvertError(Exception):
    """Raised when a volume cannot be converted to a renderable surface-mesh GLB."""


def _load_volume(src_path: str) -> tuple[np.ndarray, tuple[float, float, float]]:
    """Return (3-D float array, (z,y,x) voxel spacing). Dispatch by extension."""
    p = Path(src_path)
    suffixes = "".join(p.suffixes).lower()
    if p.is_dir():
        import tifffile

        slices = sorted(p.glob("*.tif")) + sorted(p.glob("*.tiff"))
        if not slices:
            raise VolumeConvertError(f"{src_path}: no .tif/.tiff slices in directory")
        try:
            vol = np.stack([tifffile.imread(str(s)) for s in slices])
        except Exception as e:  # noqa: BLE001
            raise VolumeConvertError(f"{src_path}: unreadable TIFF slices: {e}") from e
        return vol.astype(np.float32), (1.0, 1.0, 1.0)
    if suffixes.endswith(".nii") or suffixes.endswith(".nii.gz"):
        import nibabel as nib

        try:
            img = nib.load(src_path)
            vol = np.asarray(img.get_fdata(), dtype=np.float32)
        except Exception as e:  # noqa: BLE001
            raise VolumeConvertError(f"{src_path}: unreadable NIfTI: {e}") from e
        zooms = img.header.get_zooms()
        spacing = tuple(float(z) for z in zooms[:3]) if len(zooms) >= 3 else (1.0, 1.0, 1.0)
        return vol, spacing
    if suffixes.endswith(".tif") or suffixes.endswith(".tiff"):
        import tifffile

        try:
            vol = np.asarray(tifffile.imread(src_path), dtype=np.float32)
        except Exception as e:  # noqa: BLE001
            raise VolumeConvertError(f"{src_path}: unreadable TIFF: {e}") from e
        if vol.ndim != 3:
            raise VolumeConvertError(f"{src_path}: expected a 3-D TIFF stack, got shape {vol.shape}")
        return vol, (1.0, 1.0, 1.0)
    raise VolumeConvertError(f"{src_path}: unsupported volume format (need .nii/.nii.gz/.tif/.tiff)")


def volume_to_glb(
    src_path: str,
    *,
    threshold: float | None = None,
    max_faces: int = 200_000,
    step_size: int = 1,
) -> bytes:
    """Load a 3-D volume and export a marching-cubes surface-mesh GLB.

    threshold: iso-level. None → Otsu (skimage.filters.threshold_otsu). For low-contrast MRI
    a caller-supplied absolute level is often better; the mesh is threshold-dependent and so is
    an approximate, honest surface, not a polished asset.
    max_faces: quadric-decimate above this budget so the GLB stays web-viable.
    step_size: marching-cubes downsample stride (>1 for very large volumes).

    Raises VolumeConvertError on unsupported format, unreadable/flat volume, or empty surface.
    """
    from skimage import filters, measure

    vol, spacing = _load_volume(src_path)
    if vol.size == 0 or float(vol.min()) == float(vol.max()):
        raise VolumeConvertError(f"{src_path}: flat/empty volume, no surface to extract")

    if threshold is None:
        try:
            threshold = float(filters.threshold_otsu(vol))
        except ValueError as e:
            raise VolumeConvertError(f"{src_path}: cannot auto-threshold: {e}") from e
    if not (float(vol.min()) < threshold < float(vol.max())):
        raise VolumeConvertError(f"{src_path}: threshold {threshold} outside volume range")

    try:
        verts, faces, _normals, _vals = measure.marching_cubes(
            vol, level=threshold, spacing=spacing, step_size=step_size
        )
    except (ValueError, RuntimeError) as e:
        raise VolumeConvertError(f"{src_path}: marching cubes produced no surface: {e}") from e
    if len(faces) == 0:
        raise VolumeConvertError(f"{src_path}: empty surface at threshold {threshold}")

    mesh = trimesh.Trimesh(vertices=verts, faces=faces, process=False)
    if len(mesh.faces) > max_faces:
        mesh = mesh.simplify_quadric_decimation(face_count=max_faces)
    glb = mesh.export(file_type="glb")
    if not glb:
        raise VolumeConvertError(f"{src_path}: empty GLB export")
    return glb
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_volume_convert.py -q`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add app/volume_convert.py tests/test_volume_convert.py
git commit -m "feat(volumetric): volume_to_glb — NIfTI/TIFF volume → marching-cubes mesh GLB

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Su8b5Eq8Xjb35JUmHbxnG9"
```

---

### Task 4: `source_volumetric.ingest_volumetric` (ingest core)

**Files:**

- Create: `scripts/source_volumetric.py` (core only this task; `main()` added in Task 6)
- Test: `tests/test_source_volumetric.py`

**Interfaces:**

- Consumes: `app.sourcing.VOLUMETRIC_DATASETS` (Task 2); `app.ingest.register_output` (existing: `register_output(db, task_id, generator_slug, data, ext="glb", title="", meta=None, generator_name=None) -> (ModelOutput, bool)`).
- Produces: `ingest_volumetric(db, volume_paths, *, dataset, to_glb, score_fn=None, task_title, modality="MRI", limit=5) -> dict`. Consumed by Task 6.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_source_volumetric.py`:

```python
import trimesh
from sqlalchemy import select

from app import sourcing
from app.database import SessionLocal, init_db
from app.models import Category, ModelOutput, Task
from scripts.source_volumetric import ingest_volumetric

BARLEY = "Hordeum vulgare — barley root system (3D MRI)"


def setup_module(_m):
    init_db()


def _get_or_create_task(db, title):
    if db.query(Task).filter_by(title=title).first():
        return
    cat = db.query(Category).filter_by(slug="plants").first() or Category(
        slug="plants", name="Plants"
    )
    db.add(cat)
    db.flush()
    db.add(Task(category_id=cat.id, title=title, prompt="p"))
    db.commit()


def _fake_glb(path):
    return trimesh.creation.box().export(file_type="glb")


def test_ingest_volumetric_hosts_with_provenance():
    db = SessionLocal()
    try:
        _get_or_create_task(db, BARLEY)
        report = ingest_volumetric(
            db,
            ["/x/barley_root_01.nii"],
            dataset="ipk-barley-mri",
            to_glb=_fake_glb,
            task_title=BARLEY,
            modality="MRI",
        )
        assert report["hosted"] == 1
        task = db.execute(select(Task).where(Task.title == BARLEY)).scalars().first()
        out = (
            db.execute(
                select(ModelOutput).where(
                    ModelOutput.source == "mri:ipk-barley-mri",
                    ModelOutput.task_id == task.id,
                )
            )
            .scalars()
            .all()
        )
        assert out, "no volumetric output hosted on the barley subject"
        o = out[0]
        assert sourcing.source_class(o.source) == "volumetric"
        assert o.license == "CC-BY 4.0"
        assert "barley" in (o.attribution or "").lower()
    finally:
        db.close()


def test_ingest_volumetric_skips_unconvertible():
    from app.volume_convert import VolumeConvertError

    db = SessionLocal()
    try:
        _get_or_create_task(db, BARLEY)

        def raising(path):
            raise VolumeConvertError("no surface")

        report = ingest_volumetric(
            db,
            ["/x/bad.nii"],
            dataset="ipk-barley-mri",
            to_glb=raising,
            task_title=BARLEY,
            modality="MRI",
        )
        assert report["skipped"] == 1 and report["hosted"] == 0
    finally:
        db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_source_volumetric.py -q`
Expected: FAIL (ModuleNotFoundError: No module named 'scripts.source_volumetric')

- [ ] **Step 3: Write the ingest core**

Create `scripts/source_volumetric.py`:

```python
"""Ingest a volumetric scan (CT / MRI / X-ray) as a `<modality>:<dataset>` entry — a new
sensor axis for the arena. `ingest_volumetric` is the testable core (volume→GLB + scorer
injected); `main()` (added later) wires app.volume_convert + a local volume dir.

The pilot dataset is barley root MRI (IPK e!DAL, CC-BY-4.0) — a cereal stand-in for the
verified maize-volumetric gap (no open maize anatomy volume exists). See
docs/superpowers/specs/2026-06-23-volumetric-modality-design.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app import ingest  # noqa: E402
from app.models import Task  # noqa: E402
from app.sourcing import VOLUMETRIC_DATASETS  # noqa: E402
from app.volume_convert import VolumeConvertError  # noqa: E402


def ingest_volumetric(
    db,
    volume_paths,
    *,
    dataset,
    to_glb,
    score_fn=None,
    task_title,
    modality="MRI",
    limit=5,
) -> dict:
    """Host each volume as source=`<modality.lower()>:<dataset>` on the subject task."""
    meta_d = VOLUMETRIC_DATASETS[dataset]
    source = f"{modality.lower()}:{dataset}"
    task = db.execute(select(Task).where(Task.title == task_title)).scalars().first()
    if task is None:
        raise RuntimeError(f"subject task not found: {task_title!r}")
    report = {"hosted": 0, "skipped": 0, "errors": 0, "by_depiction": {}}
    for path in list(volume_paths)[:limit]:
        vid = Path(path).stem
        try:
            try:
                glb = to_glb(path)
            except VolumeConvertError as e:
                print(f"  skip {vid}: {e}")
                report["skipped"] += 1
                continue
            depiction = "root_system"
            out, _created = ingest.register_output(
                db,
                task_id=task.id,
                generator_slug=source,
                generator_name=meta_d["name"],
                data=glb,
                ext="glb",
                title=f"{meta_d['name']} — {vid}",
                meta={
                    "depiction": depiction,
                    "dataset": dataset,
                    "modality": modality,
                    "render": "mesh",
                    "caveat": "approximate marching-cubes iso-surface (threshold-dependent); "
                    "cereal stand-in for the maize volumetric gap",
                },
            )
            out.source = source
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
        except Exception as e:  # noqa: BLE001 — one bad volume never aborts the batch
            print(f"  error {vid}: {e}")
            report["errors"] += 1
            db.rollback()
    return report
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_source_volumetric.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add scripts/source_volumetric.py tests/test_source_volumetric.py
git commit -m "feat(volumetric): ingest_volumetric core — host CT/MRI volumes with provenance

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Su8b5Eq8Xjb35JUmHbxnG9"
```

---

### Task 5: Barley-MRI subject (seed) + spotlight entry

**Files:**

- Modify: `app/seed.py` (add `seed_volumetric_subjects`; call it in `seed_all`)
- Modify: `app/spotlight.py` (add a `SPOTLIGHTS` entry)
- Test: `tests/test_spotlight_volumetric.py`

**Interfaces:**

- Consumes: `app.seed.seed_volumetric_subjects(db)`; `app.spotlight.find_spotlight`, `build_spotlight` (existing).
- Produces: a Task titled `Hordeum vulgare — barley root system (3D MRI)` under the `plants` category, and a spotlight with slug `barley-mri`. Consumed by Task 6 (real ingest needs the Task to exist).

- [ ] **Step 1: Write the failing test**

Create `tests/test_spotlight_volumetric.py`:

```python
from app.database import SessionLocal, init_db
from app.models import Task
from app.seed import seed_volumetric_subjects
from app.spotlight import build_spotlight, find_spotlight

BARLEY = "Hordeum vulgare — barley root system (3D MRI)"


def setup_module(_m):
    init_db()


def test_barley_spotlight_registered():
    spot = find_spotlight("barley-mri")
    assert spot is not None
    assert spot["task_title"] == BARLEY


def test_seed_volumetric_subjects_idempotent_and_buildable():
    db = SessionLocal()
    try:
        seed_volumetric_subjects(db)
        seed_volumetric_subjects(db)  # idempotent → no duplicate
        tasks = db.query(Task).filter_by(title=BARLEY).all()
        assert len(tasks) == 1
        page = build_spotlight(db, "barley-mri")
        assert page is not None
        assert page["title"] == BARLEY
    finally:
        db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_spotlight_volumetric.py -q`
Expected: FAIL (ImportError: cannot import name `seed_volumetric_subjects`)

- [ ] **Step 3: Add the spotlight entry**

In `app/spotlight.py`, inside the `SPOTLIGHTS` list, after the `arabidopsis` entry (the last one, before the closing `]`), add:

```python
    {
        "slug": "barley-mri",
        "task_title": "Hordeum vulgare — barley root system (3D MRI)",
        "featured": False,
        "order": 3,
        "blurb": "The volumetric sensor axis: a real 3D MRI of a barley root system, surfaced via "
        "marching cubes. A cereal stand-in — no open maize anatomy volume exists yet (logged gap). "
        "The mesh is an approximate, threshold-dependent iso-surface, not a polished asset.",
        "reference_image": None,
    },
```

- [ ] **Step 4: Add the seed function and wire it into `seed_all`**

In `app/seed.py`, add this function after `seed_synthetic_plants` (before `seed_all`):

```python
# Volumetric-modality subjects (CT/MRI). Cereal stand-in for the maize volumetric gap.
VOLUMETRIC_SUBJECTS = [
    (
        "Hordeum vulgare — barley root system (3D MRI)",
        "Volumetric MRI reference of a barley root system (marching-cubes iso-surface).",
    ),
]


def seed_volumetric_subjects(db: Session) -> dict:
    """Idempotent: ensure the 'plants' category + each volumetric subject Task exists, so a
    volumetric GLB ingested onto the subject has a home and a spotlight to surface it."""
    cat = db.execute(select(Category).where(Category.slug == "plants")).scalars().first()
    if cat is None:
        cat = Category(slug="plants", name="Plants", description="Whole plants (image→3D recon)")
        db.add(cat)
        db.flush()
    n = 0
    for title, prompt in VOLUMETRIC_SUBJECTS:
        task = (
            db.execute(select(Task).where(Task.title == title, Task.category_id == cat.id))
            .scalars()
            .first()
        )
        if task is None:
            db.add(Task(category_id=cat.id, title=title, prompt=prompt))
            n += 1
    db.flush()
    return {"subjects": n}
```

In `seed_all`, after the `seed_synthetic_plants(db)` line, add:

```python
        seed_volumetric_subjects(db)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_spotlight_volumetric.py -q`
Expected: PASS (2 passed)

- [ ] **Step 6: Run the FULL suite (no regressions)**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (all green)

- [ ] **Step 7: Commit**

```bash
git add app/seed.py app/spotlight.py tests/test_spotlight_volumetric.py
git commit -m "feat(volumetric): barley-MRI spotlight subject + idempotent seed

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Su8b5Eq8Xjb35JUmHbxnG9"
```

---

### Task 6: `main()` wiring + real barley MRI ingest

**Files:**

- Modify: `scripts/source_volumetric.py` (add `main()`)

**Interfaces:**

- Consumes: `ingest_volumetric` (Task 4), `app.volume_convert.volume_to_glb` (Task 3), `app.seed.seed_volumetric_subjects` (Task 5), `app.database.SessionLocal`.

- [ ] **Step 1: Add `main()` to `scripts/source_volumetric.py`**

Append to `scripts/source_volumetric.py`:

```python
def main() -> int:
    import argparse

    from app.database import SessionLocal
    from app.seed import seed_volumetric_subjects
    from app.volume_convert import volume_to_glb

    BARLEY_TITLE = "Hordeum vulgare — barley root system (3D MRI)"
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", default="ipk-barley-mri", choices=sorted(VOLUMETRIC_DATASETS))
    ap.add_argument("--task", default=BARLEY_TITLE, help="subject task title to attach to")
    ap.add_argument("--modality", default="MRI")
    ap.add_argument("--dir", required=True, help="local dir of volume files (.nii/.nii.gz/.tif)")
    ap.add_argument("--threshold", type=float, default=None, help="iso-level (default: Otsu)")
    ap.add_argument("--max-faces", type=int, default=200_000)
    ap.add_argument("--step", type=int, default=1, help="marching-cubes downsample stride")
    ap.add_argument("--limit", type=int, default=1)
    args = ap.parse_args()

    root = Path(args.dir)
    if not root.exists():
        print(f"volume dir not found: {root}")
        return 1
    vols = sorted(
        str(p)
        for ext in ("*.nii", "*.nii.gz", "*.tif", "*.tiff")
        for p in root.rglob(ext)
    )
    if not vols:
        print(f"no .nii/.nii.gz/.tif volumes under {root}")
        return 1

    def to_glb(path: str) -> bytes:
        return volume_to_glb(
            path, threshold=args.threshold, max_faces=args.max_faces, step_size=args.step
        )

    db = SessionLocal()
    try:
        seed_volumetric_subjects(db)  # ensure the subject Task exists
        db.commit()
        report = ingest_volumetric(
            db,
            vols,
            dataset=args.dataset,
            to_glb=to_glb,
            task_title=args.task,
            modality=args.modality,
            limit=args.limit,
        )
        print(report)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Commit the wiring**

```bash
git add scripts/source_volumetric.py
git commit -m "feat(volumetric): source_volumetric main() — fetch-dir → convert → ingest

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Su8b5Eq8Xjb35JUmHbxnG9"
```

- [ ] **Step 3: Fetch ONE barley root MRI volume from IPK e!DAL**

The dataset DOI is `10.5447/IPK/2017/10` (landing: `https://doi.org/10.5447/IPK/2017/10`). Resolve the record's file list and download a single NIfTI volume into `$CLAUDE_JOB_DIR/tmp/barley_mri/` (e!DAL exposes per-file download links from the landing page; if the file listing is JS-gated, use the e!DAL PGP download API for the record, or fall back to the DataCite `media`/`relatedIdentifiers` links). Verify the file loads:

Run: `.venv/bin/python -c "import nibabel as nib,sys; a=nib.load(sys.argv[1]).get_fdata(); print('shape', a.shape, 'range', float(a.min()), float(a.max()))" $CLAUDE_JOB_DIR/tmp/barley_mri/<file>.nii`
Expected: prints a 3-D shape and a non-flat intensity range.

> If e!DAL download proves inaccessible programmatically, STOP and report — do not substitute a different dataset without approval (the dataset choice was design-approved). The CC0 wheat/barley root CT (Harvard Dataverse `10.7910/DVN/DXG4AH`, RAW) is the pre-vetted fallback but RAW needs a dims/dtype reader (a follow-up task), so it is out of scope for this run.

- [ ] **Step 4: Run the real ingest**

Run: `.venv/bin/python scripts/source_volumetric.py --dir $CLAUDE_JOB_DIR/tmp/barley_mri --limit 1`
Expected: prints a report like `{'hosted': 1, 'skipped': 0, 'errors': 0, 'by_depiction': {'root_system': 1}}`

> If the marching-cubes mesh is empty/garbage on the real low-contrast MRI (skipped>0 or a degenerate mesh), tune `--threshold` (try a few absolute levels from the printed intensity range in Step 3) and/or `--step 2`. The default Otsu may not suit MRI.

- [ ] **Step 5: Verify in the DB**

Run:

```bash
.venv/bin/python -c "
import sqlite3
c=sqlite3.connect('data/arena.db')
for r in c.execute(\"select source,title,license from model_output mo join task t on mo.task_id=t.id where t.title like 'Hordeum%'\"):
    print(r)
"
```

Expected: one row, `source='mri:ipk-barley-mri'`, CC-BY-4.0.

- [ ] **Step 6: Final full-suite check**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (all green)

---

## Notes for the executor

- The real-ingest steps (Task 6 Steps 3–5) touch the network + the worktree DB; they are not unit-tested. The DB write is small (one output) — no jobd needed (not heavy/GPU).
- After all tasks: update `~/.claude/projects/-home-mjarnold-bio3d-arena/memory/maize_coverage_status.md` (Tier 3 status + the maize-volumetric gap), and the canonical plan, then offer to fast-forward merge `worktree-bio3d-arena-mvp` → `master` (do NOT remove the worktree).
