# Bio 3D Arena — Field-Audit Plan of Attack

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute the 2026-06-20 field audit (`docs/audits/2026-06-20-field-audit.md`) as a sequence of independently-shippable increments. This document is the **roadmap for all 7 increments** plus the **full executable plan for Increment 1** (real benchmark content + SDF support). Increments 2–7 are sequenced here with deliverables/dependencies; each gets its own detailed plan when we reach it (writing them all now would go stale).

**Architecture:** Keep the established pattern — server-rendered FastAPI + Jinja2 + vanilla JS, SQLAlchemy 2.0 over SQLite (Postgres via `DATABASE_URL`), format-keyed client-side viewer registry (`window.Bio3DViewer`). Each increment is TDD'd, live-verified under uvicorn, committed, and fast-forward-merged to `master`.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2.0, trimesh, numpy, 3Dmol.js, Google `<model-viewer>`, pytest.

## Global Constraints

- **Test runner:** `.venv/bin/python -m pytest` (the base conda env lacks sqlalchemy; the project `.venv` has it). Never assert green from the base interpreter.
- **Lint:** `ruff check app/ tests/` must pass; `ruff --fix` runs as a PostToolUse hook and **strips imports added before their first use** — add an import and its usage in the same edit, and re-grep the import after edits.
- **Templates:** prettier mangles Jinja `==`; `app/templates/` is in `.prettierignore`. Never write `{% if x == 'y' %}` — precompute a `selected`/boolean flag server-side and write `{% if opt.flag %}`.
- **Format registry is mirrored in two places that must stay in sync:** `app/ingest.py` (`MESH_FORMATS` / `MOLECULAR_FORMATS` / `ALLOWED_FORMATS`, Python) and `app/static/viewer.js` (`MESH` / `MOL` Sets, JS). Any new format is added to BOTH.
- **Asset provenance** goes in `model_output.meta_json` (free-form Text) — no schema migration for source/license/attribution.
- **Licensing:** every bundled or fetched real asset records `source`, `license`, `attribution` in its meta. Commit only CC0 / public-domain / permissive (BSD/CC-BY-with-attribution) assets; never mirror registration-gated data (PDBBind/CASF) — link out.
- **Commits** end with the trailers:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`
  `Claude-Session: https://claude.ai/code/session_01N8JSB5JnrqT2N1hNUeYYoW`
- **Merge** each increment to master from `~/bio3d-arena` via `git merge --ff-only worktree-bio3d-arena-mvp`.
- After each increment, **flip the corresponding `[ ]→[x]` checkboxes in `docs/audits/2026-06-20-field-audit.md`** (status lives in the audit doc).

---

## Roadmap — all 7 increments

Ordered by leverage-per-effort (audit §E). Each row is one shippable increment in the test → live-verify → commit → merge pattern.

| #     | Increment                                               | Audit refs                                       | Delivers                                                                                                                                                                       | Depends on                                  | Rough size         |
| ----- | ------------------------------------------------------- | ------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------- | ------------------ |
| **1** | **Real benchmark content + SDF support**                | C1, C3-SDF, B5-meta                              | SDF end-to-end; benchmark manifest + loader + fetch script; real CC0 content offline                                                                                           | —                                           | **detailed below** |
| 2     | Leaderboard credibility surface                         | B1 (CI-rank, CI bars, vote counts, tie handling) | "Rank (Upper Bound)" + per-row CI whisker + `n_games`; verified tie handling in BT fit                                                                                         | —                                           | S (½–1 day)        |
| 3     | Viewer affordances + a11y pass                          | D1, D2, D4                                       | drag hint, loading spinner, asset-failure fallback, reset/fullscreen; focus styles, AA contrast, favicon, drop "MVP", admin out of public nav, colorblind-safe matrix + legend | needs headless browser to screenshot-verify | M (1 day)          |
| 4     | Structure-validation track (the moat)                   | B3                                               | per-output validity badges (TM-score / MolProbity-style clashscore / Ramachandran) + a "is it physically valid?" view distinct from the aesthetic vote                         | 1 (real PDB content to validate against)    | M–L                |
| 5     | Transparency: vote-data export + read API + model cards | B4, B5                                           | anonymized vote dump (CSV/Parquet) + recompute notebook; JSON leaderboard API; per-model metadata cards (license/format/provider)                                              | 1 (meta fields)                             | S–M                |
| 6     | Engagement: embeddable rank badge                       | B6                                               | dynamic SVG/PNG "#N on Bio 3D Arena" badge endpoint + iframe mini-leaderboard + OG cards                                                                                       | 2 (rank numbers)                            | S                  |
| 7     | Voxel→GLB ingest pipeline                               | C3-voxel                                         | offline `niftiitomesh`/marching-cubes converter → register CellMap/TotalSegmentator/MSD organ meshes                                                                           | 1 (loader)                                  | L                  |

**Cross-cutting prerequisite (do before Increment 3):** install a headless browser so the visual fixes are screenshot-verified, not reasoned-about — `pip install playwright && playwright install chromium`. Until then, Increment 3's "done" is gated on real screenshots.

**Methodology note for Increments 2 & 4:** before changing the BT fit (tie handling, style control), read `app/ranking.py` + `app/service.py` and confirm how ties currently flow — the audit flags "verify ties aren't silently dropped" as unconfirmed.

---

# Increment 1 — Real benchmark content + SDF support

**Why first:** the platform is hollow without real tasks, and SDF is the single format add that unlocks the entire docking/conformer/SBDD corpus (PoseBusters, CrossDocked, GEOM). Both are independently testable.

**Approach:** (a) add `sdf`/`mol` as a molecular subformat through the existing validate → store → serve → view pipeline; (b) add a **benchmark manifest + loader** that registers real, openly-licensed assets (bundled small CC0 examples committed to the repo so the arena has real content offline) with `source`/`license`/`attribution` provenance; (c) a network-gated `scripts/fetch_benchmarks.py` to populate the larger corpora at deploy time.

### File Structure

- Modify `app/ingest.py` — add `SDF_FORMATS = {"sdf", "mol"}`, fold into `MOLECULAR_FORMATS`/`ALLOWED_FORMATS`, add `_validate_sdf`, route it in `validate_asset`.
- Modify `app/storage.py` — content-types for `sdf`/`mol`.
- Modify `app/static/viewer.js` — add `sdf`/`mol` to `MOL`; map them to 3Dmol model type `"sdf"`.
- Modify `app/molec_gen.py` — add `build_molecule_sdf(seed, out_path)` (demo asset, mirrors `build_molecule_pdb`).
- Modify `app/seed.py` — add one `sdf` demo task wired end-to-end (mirrors the `ligand-pdb` task).
- Create `app/benchmarks.py` — manifest schema + loader (`load_manifest`, `register_benchmark_entry`, `load_benchmarks`).
- Create `app/data/benchmarks/manifest.json` — curated entries pointing at bundled assets.
- Create `app/data/benchmarks/assets/` — small real CC0 assets (one PDB, one SDF) committed to the repo.
- Create `scripts/fetch_benchmarks.py` — network-gated downloader for larger open corpora.
- Create `tests/test_sdf.py`, `tests/test_benchmarks.py`.

### Interfaces (produced this increment, relied on later)

- `ingest.validate_asset(data: bytes, ext: str) -> dict` — now accepts `sdf`/`mol`; returns `{"kind":"molecular","atoms":int,"molecules":int,"subformat":"sdf"}`.
- `molec_gen.build_molecule_sdf(seed: int, out_path: Path) -> dict` — writes a valid V2000 SDF, returns `{"format":"sdf","seed":int,"atoms":int,"generated":True}`.
- `benchmarks.load_benchmarks(db, manifest_path: Path, assets_dir: Path) -> dict` — registers all manifest entries, returns `{"tasks":int,"outputs":int,"skipped":int}`.

---

### Task 1: SDF/MOL validation in the ingest pipeline

**Files:**

- Modify: `app/ingest.py:25-49` (format sets + `validate_asset` routing) and add `_validate_sdf`
- Test: `tests/test_sdf.py`

**Interfaces:**

- Consumes: nothing new.
- Produces: `validate_asset(data, "sdf")` → dict with `subformat="sdf"`; `IngestError` on zero-atom/short input.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sdf.py
"""Tests for SDF/MOL molecular subformat support."""

from __future__ import annotations

import pytest

from app import ingest

VALID_SDF = """arena-test
  Bio3DArena

  3  2  0  0  0  0  0  0  0  0999 V2000
    0.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
    1.5000    0.0000    0.0000 O   0  0  0  0  0  0  0  0  0  0  0  0
    0.0000    1.5000    0.0000 N   0  0  0  0  0  0  0  0  0  0  0  0
  1  2  1  0  0  0  0
  1  3  1  0  0  0  0
M  END
$$$$
"""


def test_sdf_in_allowed_formats():
    assert "sdf" in ingest.ALLOWED_FORMATS
    assert "sdf" in ingest.MOLECULAR_FORMATS  # routed to the 3Dmol viewer


def test_validate_sdf_ok():
    stats = ingest.validate_asset(VALID_SDF.encode(), "sdf")
    assert stats["kind"] == "molecular"
    assert stats["subformat"] == "sdf"
    assert stats["atoms"] == 3
    assert stats["molecules"] == 1


def test_validate_sdf_zero_atoms_rejected():
    bad = "x\n\n\n  0  0  0  0  0  0  0  0  0  0999 V2000\nM  END\n$$$$\n"
    with pytest.raises(ingest.IngestError):
        ingest.validate_asset(bad.encode(), "sdf")


def test_validate_sdf_garbage_rejected():
    with pytest.raises(ingest.IngestError):
        ingest.validate_asset(b"not a molfile", "sdf")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_sdf.py -q`
Expected: FAIL — `"sdf"` not in `ALLOWED_FORMATS` / `validate_asset` raises "Unsupported format 'sdf'".

- [ ] **Step 3: Implement SDF support in `app/ingest.py`**

Replace the format-set block (lines 25-27) with:

```python
MESH_FORMATS = {"glb", "gltf"}  # rendered by <model-viewer>
PDB_FORMATS = {"pdb", "cif", "mmcif", "ent"}  # rendered by 3Dmol.js (atomic coords)
SDF_FORMATS = {"sdf", "mol"}  # rendered by 3Dmol.js (connection-table molfiles)
MOLECULAR_FORMATS = PDB_FORMATS | SDF_FORMATS
ALLOWED_FORMATS = MESH_FORMATS | MOLECULAR_FORMATS
```

In `validate_asset` (the `if ext in MOLECULAR_FORMATS` branch), split SDF out. Replace the body:

```python
def validate_asset(data: bytes, ext: str) -> dict:
    """Validate an asset by format family and return provenance stats.

    Meshes (GLB/GLTF) must load with geometry; PDB/mmCIF must contain atom
    records; SDF/MOL must have a V2000/V3000 counts line with ≥1 atom. Raises
    IngestError on unknown/unparseable/empty assets.
    """
    ext = ext.lower()
    if ext in MESH_FORMATS:
        return _validate_mesh(data, ext)
    if ext in SDF_FORMATS:
        return _validate_sdf(data, ext)
    if ext in PDB_FORMATS:
        return _validate_molecular(data, ext)
    raise IngestError(f"Unsupported format '{ext}'. Arena renders {sorted(ALLOWED_FORMATS)}.")
```

Add the validator (after `_validate_molecular`):

```python
def _validate_sdf(data: bytes, ext: str) -> dict:
    """Validate an MDL MOL/SDF connection-table file (V2000 or V3000)."""
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    # MOL/SDF layout: 3 header lines, then the counts line.
    if len(lines) < 4:
        raise IngestError("SDF/MOL too short — missing the counts line.")
    counts = lines[3]
    if "V2000" not in counts and "V3000" not in counts:
        raise IngestError("SDF/MOL counts line missing V2000/V3000 tag.")
    n_atoms = 0
    if "V3000" in counts:
        for ln in lines:
            if ln.strip().startswith("M  V30 COUNTS"):
                try:
                    n_atoms = int(ln.split()[3])
                except (IndexError, ValueError):
                    n_atoms = 0
                break
    else:  # V2000 packs atom count in the first 3 columns of the counts line
        try:
            n_atoms = int(counts[:3])
        except ValueError:
            n_atoms = 0
    if n_atoms <= 0:
        raise IngestError("SDF/MOL has zero atoms.")
    n_mols = text.count("$$$$") or 1
    return {"kind": "molecular", "atoms": n_atoms, "molecules": n_mols, "subformat": "sdf"}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_sdf.py -q && ruff check app/ingest.py tests/test_sdf.py`
Expected: PASS; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add app/ingest.py tests/test_sdf.py
git commit -m "feat(ingest): validate SDF/MOL molecular files

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01N8JSB5JnrqT2N1hNUeYYoW"
```

---

### Task 2: Serve + render SDF (content-type + viewer registry)

**Files:**

- Modify: `app/storage.py:16-23` (`_CONTENT_TYPES`)
- Modify: `app/static/viewer.js:5-6,20-37` (`MOL` set + model-type mapping)
- Test: `tests/test_sdf.py` (extend)

**Interfaces:**

- Consumes: `ingest.SDF_FORMATS`.
- Produces: `storage.content_type_for("x.sdf") == "chemical/x-mdl-sdfile"`; viewer routes `sdf`/`mol` to 3Dmol model type `"sdf"`.

- [ ] **Step 1: Write the failing test (append to `tests/test_sdf.py`)**

```python
def test_sdf_content_type():
    from app import storage

    assert storage.content_type_for("ligand.sdf") == "chemical/x-mdl-sdfile"
    assert storage.content_type_for("frag.mol") == "chemical/x-mdl-molfile"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_sdf.py::test_sdf_content_type -q`
Expected: FAIL — returns `application/octet-stream`.

- [ ] **Step 3: Add content-types in `app/storage.py`**

Extend `_CONTENT_TYPES` (after the `mmcif` entry):

```python
    "mmcif": "chemical/x-cif",
    "sdf": "chemical/x-mdl-sdfile",
    "mol": "chemical/x-mdl-molfile",
```

- [ ] **Step 4: Update the viewer registry `app/static/viewer.js`**

Change the `MOL` set (line 6):

```javascript
const MOL = new Set(["pdb", "cif", "mmcif", "ent", "sdf", "mol"]);
```

Change the model-type mapping inside `mountMolecular` (line 25). Replace:

```javascript
const modelType = fmt === "cif" || fmt === "mmcif" ? "cif" : "pdb";
```

with:

```javascript
let modelType = "pdb";
if (fmt === "cif" || fmt === "mmcif") modelType = "cif";
else if (fmt === "sdf" || fmt === "mol") modelType = "sdf";
```

(3Dmol's `addModel(text, "sdf")` parses the connection table directly — do NOT convert SDF→PDB; that drops bond orders/stereo.)

- [ ] **Step 5: Run test + lint**

Run: `.venv/bin/python -m pytest tests/test_sdf.py -q && ruff check app/storage.py`
Expected: PASS; ruff clean. (viewer.js is formatted by the prettier hook — no `==` concern in JS.)

- [ ] **Step 6: Commit**

```bash
git add app/storage.py app/static/viewer.js tests/test_sdf.py
git commit -m "feat(viewer): serve + render SDF/MOL via 3Dmol

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01N8JSB5JnrqT2N1hNUeYYoW"
```

---

### Task 3: Demo SDF generator + seed an SDF task end-to-end

**Files:**

- Modify: `app/molec_gen.py` (add `build_molecule_sdf`)
- Modify: `app/seed.py:32` (import), `:72-121` (`TASKS`), `:182-189` (asset-build branch)
- Test: `tests/test_sdf.py` (extend)

**Interfaces:**

- Consumes: `validate_asset` (Task 1).
- Produces: `build_molecule_sdf(seed, out_path) -> dict`; a seeded `ligand-sdf` task whose outputs have `asset_format="sdf"`.

- [ ] **Step 1: Write the failing test (append to `tests/test_sdf.py`)**

```python
def test_build_molecule_sdf_roundtrips_through_validation(tmp_path):
    from app import ingest
    from app.molec_gen import build_molecule_sdf

    out = tmp_path / "demo.sdf"
    meta = build_molecule_sdf(7, out)
    assert meta["format"] == "sdf"
    assert meta["atoms"] >= 4
    # The generated asset must pass our own ingest validator.
    stats = ingest.validate_asset(out.read_bytes(), "sdf")
    assert stats["atoms"] == meta["atoms"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_sdf.py::test_build_molecule_sdf_roundtrips_through_validation -q`
Expected: FAIL — `cannot import name 'build_molecule_sdf'`.

- [ ] **Step 3: Implement `build_molecule_sdf` in `app/molec_gen.py`**

Append:

```python
def build_molecule_sdf(seed: int, out_path: Path) -> dict:
    """Write a small connected molecule as a V2000 SDF. Returns provenance meta.

    A chain of atoms with single bonds — valid MDL molfile that 3Dmol renders as
    ball-and-stick. Element/length vary deterministically by seed.
    """
    rng = np.random.default_rng(seed)
    n = int(rng.integers(4, 9))
    coords = np.cumsum(rng.normal(0.0, 1.4, size=(n, 3)), axis=0)
    elements = [_ELEMENTS[int(rng.integers(0, len(_ELEMENTS)))] for _ in range(n)]
    bonds = [(i, i + 1) for i in range(1, n)]  # 1-indexed chain

    header = ["arena-demo", "  Bio3DArena", ""]
    counts = f"{n:>3}{len(bonds):>3}  0  0  0  0  0  0  0  0999 V2000"
    atom_lines = [
        f"{c[0]:>10.4f}{c[1]:>10.4f}{c[2]:>10.4f} {el:<3} 0  0  0  0  0  0  0  0  0  0  0  0"
        for c, el in zip(coords, elements)
    ]
    bond_lines = [f"{a:>3}{b:>3}  1  0  0  0  0" for a, b in bonds]
    block = header + [counts] + atom_lines + bond_lines + ["M  END", "$$$$"]
    text = "\n".join(block) + "\n"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text)
    return {"format": "sdf", "seed": int(seed), "atoms": n, "generated": True}
```

- [ ] **Step 4: Wire an SDF task into `app/seed.py`**

In the import on line 32, extend:

```python
from .molec_gen import build_molecule_pdb, build_molecule_sdf
```

Add a task to the `TASKS` list (after the `ligand-pdb` tuple, before the closing `]`):

```python
    (
        "ligand-sdf",
        "molecules",
        "Small molecule (SDF/molfile)",
        "Generate a small organic molecule as an MDL SDF connection table.",
        "molecule",
        "sdf",
    ),
```

In the seed loop, generalize the asset-build branch. Replace lines 182-189:

```python
            ext = {"pdb": "pdb", "sdf": "sdf"}.get(kind, "glb")
            for gslug, gen in gens.items():
                seed = _seed_int(tslug, gslug)
                rel = Path("seed") / f"{tslug}__{gslug}.{ext}"
                if kind == "pdb":
                    meta = build_molecule_pdb(seed, config.ASSET_DIR / rel)
                elif kind == "sdf":
                    meta = build_molecule_sdf(seed, config.ASSET_DIR / rel)
                else:
                    meta = build_asset(shape, seed, config.ASSET_DIR / rel)
```

- [ ] **Step 5: Add an end-to-end seed assertion (append to `tests/test_sdf.py`)**

```python
def test_seed_creates_sdf_outputs():
    from app.database import SessionLocal
    from app.models import ModelOutput
    from app.seed import seed_all

    seed_all(force=True)
    with SessionLocal() as db:
        n = db.query(ModelOutput).filter_by(asset_format="sdf", is_gold=False).count()
    assert n >= 1  # the ligand-sdf task produced SDF outputs
```

- [ ] **Step 6: Run tests + lint**

Run: `.venv/bin/python -m pytest tests/test_sdf.py -q && ruff check app/molec_gen.py app/seed.py`
Expected: PASS; ruff clean. **Re-grep** that `build_molecule_sdf` survived in the seed import (ruff may strip it if the usage edit landed separately): `grep build_molecule_sdf app/seed.py`.

- [ ] **Step 7: Commit**

```bash
git add app/molec_gen.py app/seed.py tests/test_sdf.py
git commit -m "feat(seed): demo SDF generator + ligand-sdf benchmark task

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01N8JSB5JnrqT2N1hNUeYYoW"
```

---

### Task 4: Benchmark manifest schema + loader

**Files:**

- Create: `app/benchmarks.py`
- Test: `tests/test_benchmarks.py`

**Interfaces:**

- Consumes: `ingest.upsert_category`, `ingest.create_task`, `ingest.register_output`.
- Produces:
  - `load_manifest(path: Path) -> list[dict]`
  - `register_benchmark_entry(db, entry: dict, assets_dir: Path) -> tuple[int, bool]` → `(model_output_id, created)`
  - `load_benchmarks(db, manifest_path: Path, assets_dir: Path) -> dict` → `{"tasks":int,"outputs":int,"skipped":int}`

A manifest entry is:

```json
{
  "task_slug": "rnapuzzle-pz1",
  "category": "molecules",
  "title": "...",
  "prompt": "...",
  "generator_slug": "rnapuzzles-native",
  "generator_name": "RNA-Puzzles native",
  "file": "assets/pz1_native.pdb",
  "format": "pdb",
  "source": "https://www.rcsb.org/structure/3MEI",
  "license": "CC0",
  "attribution": "RCSB PDB"
}
```

- [ ] **Step 1: Write the failing test**

```python
# tests/test_benchmarks.py
"""Tests for the benchmark manifest loader."""

from __future__ import annotations

import json

from app import benchmarks
from app.database import SessionLocal
from app.models import Category, ModelOutput, Task
from app.molec_gen import build_molecule_sdf
from app.seed import seed_all


def setup_module(_module):
    seed_all(force=True)  # categories exist


def _fixture(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    build_molecule_sdf(1, assets / "lig.sdf")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            [
                {
                    "task_slug": "bench-lig",
                    "category": "molecules",
                    "title": "Benchmark ligand",
                    "prompt": "A curated benchmark ligand.",
                    "generator_slug": "bench-native",
                    "generator_name": "Benchmark native",
                    "file": "assets/lig.sdf",
                    "format": "sdf",
                    "source": "https://example.org/lig",
                    "license": "CC0",
                    "attribution": "Example",
                }
            ]
        )
    )
    return manifest, assets


def test_load_manifest_parses(tmp_path):
    manifest, _ = _fixture(tmp_path)
    entries = benchmarks.load_manifest(manifest)
    assert entries[0]["task_slug"] == "bench-lig"


def test_load_benchmarks_registers_task_and_output(tmp_path):
    manifest, assets = _fixture(tmp_path)
    with SessionLocal() as db:
        summary = benchmarks.load_benchmarks(db, manifest, assets)
        db.commit()
        assert summary["outputs"] == 1
        out = db.query(ModelOutput).join(Task).filter(Task.title == "Benchmark ligand").one()
        assert out.asset_format == "sdf"
        meta = json.loads(out.meta_json)
        assert meta["license"] == "CC0"
        assert meta["source"].startswith("http")


def test_load_benchmarks_idempotent(tmp_path):
    manifest, assets = _fixture(tmp_path)
    with SessionLocal() as db:
        benchmarks.load_benchmarks(db, manifest, assets)
        db.commit()
        second = benchmarks.load_benchmarks(db, manifest, assets)
        db.commit()
    assert second["outputs"] == 0  # same bytes → dedup, nothing new
    assert second["skipped"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_benchmarks.py -q`
Expected: FAIL — `No module named 'app.benchmarks'`.

- [ ] **Step 3: Implement `app/benchmarks.py`**

```python
"""Curated benchmark loader — register real, openly-licensed 3D assets as tasks.

A manifest (JSON list of entries) maps a curated asset file to a (category, task,
generator). Each entry records source/license/attribution provenance, stored in
model_output.meta_json. Idempotent via the content-hash dedup in register_output.
"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import ingest
from .models import Task

REQUIRED_FIELDS = ("task_slug", "category", "title", "prompt", "generator_slug", "file", "format")


def load_manifest(path: Path) -> list[dict]:
    """Parse + lightly validate a benchmark manifest. Raises on malformed entries."""
    entries = json.loads(Path(path).read_text())
    if not isinstance(entries, list):
        raise ingest.IngestError("Manifest must be a JSON list of entries.")
    for i, e in enumerate(entries):
        missing = [f for f in REQUIRED_FIELDS if f not in e]
        if missing:
            raise ingest.IngestError(f"Manifest entry {i} missing fields: {missing}")
    return entries


def _task_for_slug(db: Session, entry: dict) -> Task:
    """Find-or-create the task for an entry, keyed by a synthetic title match."""
    existing = db.execute(select(Task).where(Task.title == entry["title"])).scalars().first()
    if existing is not None:
        return existing
    ingest.upsert_category(db, entry["category"])
    return ingest.create_task(
        db,
        category_slug=entry["category"],
        title=entry["title"],
        prompt=entry["prompt"],
        criteria_note=entry.get("criteria_note", ""),
    )


def register_benchmark_entry(db: Session, entry: dict, assets_dir: Path) -> tuple[int, bool]:
    """Register one manifest entry's asset as a ModelOutput. Returns (output_id, created)."""
    data = (Path(assets_dir) / entry["file"]).read_bytes()
    task = _task_for_slug(db, entry)
    meta = {
        "benchmark": True,
        "source": entry.get("source", ""),
        "license": entry.get("license", ""),
        "attribution": entry.get("attribution", ""),
    }
    output, created = ingest.register_output(
        db,
        task_id=task.id,
        generator_slug=entry["generator_slug"],
        data=data,
        ext=entry["format"],
        title=entry.get("output_title", ""),
        meta=meta,
        generator_name=entry.get("generator_name"),
    )
    return output.id, created


def load_benchmarks(db: Session, manifest_path: Path, assets_dir: Path) -> dict:
    """Register every entry in a manifest. Idempotent (content-hash dedup)."""
    entries = load_manifest(manifest_path)
    tasks: set[str] = set()
    outputs = skipped = 0
    for entry in entries:
        _, created = register_benchmark_entry(db, entry, assets_dir)
        tasks.add(entry["title"])
        if created:
            outputs += 1
        else:
            skipped += 1
    db.flush()
    return {"tasks": len(tasks), "outputs": outputs, "skipped": skipped}
```

- [ ] **Step 4: Run tests + lint**

Run: `.venv/bin/python -m pytest tests/test_benchmarks.py -q && ruff check app/benchmarks.py tests/test_benchmarks.py`
Expected: PASS; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add app/benchmarks.py tests/test_benchmarks.py
git commit -m "feat(benchmarks): manifest loader for curated open assets

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01N8JSB5JnrqT2N1hNUeYYoW"
```

---

### Task 5: Real CC0 content bundle + network-gated fetch script

**Files:**

- Create: `app/data/benchmarks/assets/` (bundled real CC0 assets)
- Create: `app/data/benchmarks/manifest.json`
- Create: `scripts/fetch_benchmarks.py`
- Test: `tests/test_benchmarks.py` (extend with a real-bundle smoke test)

**Interfaces:**

- Consumes: `benchmarks.load_benchmarks`.
- Produces: a committed manifest + assets that `load_benchmarks` registers offline; a documented fetch command for larger corpora.

- [ ] **Step 1: Fetch two small real CC0 structures (RCSB PDB is CC0)**

Run (these are small, openly-licensed; if the network is unavailable, see Step 1b fallback):

```bash
mkdir -p app/data/benchmarks/assets
# A small protein-ligand structure (PDB, CC0) and its ligand (the ligand is the SDF side).
curl -sL "https://files.rcsb.org/download/1CRN.pdb" -o app/data/benchmarks/assets/1crn.pdb   # crambin, 46 residues
curl -sL "https://files.rcsb.org/ligands/download/HEM_ideal.sdf" -o app/data/benchmarks/assets/hem.sdf  # heme ligand, ideal coords
ls -l app/data/benchmarks/assets/
# Sanity: both must pass our validators.
.venv/bin/python -c "from app import ingest; from pathlib import Path; \
print(ingest.validate_asset(Path('app/data/benchmarks/assets/1crn.pdb').read_bytes(),'pdb')); \
print(ingest.validate_asset(Path('app/data/benchmarks/assets/hem.sdf').read_bytes(),'sdf'))"
```

- [ ] **Step 1b: Fallback if RCSB is unreachable**

If `curl` fails (no network), generate stand-in assets so the increment still ships, and mark them `"license":"generated"` in the manifest (NOT real benchmarks — replace later via the fetch script):

```bash
.venv/bin/python -c "from pathlib import Path; from app.molec_gen import build_molecule_pdb, build_molecule_sdf; \
build_molecule_pdb(101, Path('app/data/benchmarks/assets/1crn.pdb')); \
build_molecule_sdf(102, Path('app/data/benchmarks/assets/hem.sdf'))"
```

State in the commit body which path was taken.

- [ ] **Step 2: Write `app/data/benchmarks/manifest.json`**

```json
[
  {
    "task_slug": "crambin-fold",
    "category": "proteins",
    "title": "Crambin (1CRN) — real fold reference",
    "prompt": "A real small protein structure (crambin, 46 residues) as a benchmark reference fold.",
    "generator_slug": "rcsb-experimental",
    "generator_name": "RCSB experimental",
    "file": "assets/1crn.pdb",
    "format": "pdb",
    "source": "https://www.rcsb.org/structure/1CRN",
    "license": "CC0",
    "attribution": "RCSB PDB (1CRN)"
  },
  {
    "task_slug": "heme-ligand",
    "category": "molecules",
    "title": "Heme (HEM) — real ligand reference",
    "prompt": "A real small-molecule ligand (heme) as an SDF connection-table reference.",
    "generator_slug": "rcsb-ligand",
    "generator_name": "RCSB ligand",
    "file": "assets/hem.sdf",
    "format": "sdf",
    "source": "https://www.rcsb.org/ligand/HEM",
    "license": "CC0",
    "attribution": "RCSB PDB Chemical Component Dictionary (HEM)"
  }
]
```

- [ ] **Step 3: Write `scripts/fetch_benchmarks.py` (network-gated corpus downloader)**

```python
"""Download larger open benchmark corpora into app/data/benchmarks/assets/.

Network-gated (run at deploy/curation time, NOT in tests). Extends manifest.json
with fetched entries. Records source/license/attribution per the audit's license
watch. Currently wired for RCSB PDB (CC0) IDs and ligand SDFs; extend SOURCES for
RNA-Puzzles, CAMEO, HuBMAP HRA, etc.

Usage: .venv/bin/python scripts/fetch_benchmarks.py
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "app" / "data" / "benchmarks" / "assets"
MANIFEST = ROOT / "app" / "data" / "benchmarks" / "manifest.json"

# (pdb_id, category, title, prompt) — RCSB structures are CC0.
PDB_SOURCES = [
    ("1UBQ", "proteins", "Ubiquitin (1UBQ) — real fold reference",
     "Ubiquitin, 76 residues — a benchmark reference fold."),
]


def _fetch(url: str, dest: Path) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=30) as r:  # noqa: S310 — trusted RCSB host
            dest.write_bytes(r.read())
        return True
    except Exception as exc:  # noqa: BLE001 — surface the fetch failure, keep going
        print(f"  ! failed {url}: {exc}", file=sys.stderr)
        return False


def main() -> int:
    ASSETS.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else []
    have = {e["file"] for e in manifest}
    for pdb_id, category, title, prompt in PDB_SOURCES:
        rel = f"assets/{pdb_id.lower()}.pdb"
        if rel in have:
            continue
        if _fetch(f"https://files.rcsb.org/download/{pdb_id}.pdb", ASSETS / f"{pdb_id.lower()}.pdb"):
            manifest.append({
                "task_slug": f"{pdb_id.lower()}-fold", "category": category,
                "title": title, "prompt": prompt,
                "generator_slug": "rcsb-experimental", "generator_name": "RCSB experimental",
                "file": rel, "format": "pdb",
                "source": f"https://www.rcsb.org/structure/{pdb_id}",
                "license": "CC0", "attribution": f"RCSB PDB ({pdb_id})",
            })
            print(f"  + {pdb_id}")
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"manifest now has {len(manifest)} entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Add a real-bundle smoke test (append to `tests/test_benchmarks.py`)**

```python
def test_bundled_manifest_loads(tmp_path):
    from pathlib import Path

    bench_dir = Path("app/data/benchmarks")
    with SessionLocal() as db:
        summary = benchmarks.load_benchmarks(db, bench_dir / "manifest.json", bench_dir)
        db.rollback()  # don't pollute the shared seeded DB for other tests
    assert summary["tasks"] >= 2  # crambin + heme
```

- [ ] **Step 5: Run tests + lint**

Run: `.venv/bin/python -m pytest tests/test_benchmarks.py -q && ruff check scripts/fetch_benchmarks.py`
Expected: PASS; ruff clean.

- [ ] **Step 6: Commit**

```bash
git add app/data/benchmarks scripts/fetch_benchmarks.py tests/test_benchmarks.py
git commit -m "feat(benchmarks): bundle real CC0 assets + corpus fetch script

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01N8JSB5JnrqT2N1hNUeYYoW"
```

---

### Task 6: Wire benchmark loading into seed + live-verify + docs

**Files:**

- Modify: `app/seed.py` (call `load_benchmarks` at the end of `seed_all`)
- Modify: `README.md` (document SDF + benchmark loading)
- Modify: `docs/audits/2026-06-20-field-audit.md` (flip C1/C3-SDF checkboxes)
- Test: full suite

**Interfaces:**

- Consumes: `benchmarks.load_benchmarks`.
- Produces: a seeded DB that includes the bundled real benchmarks.

- [ ] **Step 1: Load benchmarks during seed (`app/seed.py`)**

Before `db.commit()` in `seed_all` (after `_seed_gold`), add:

```python
        # Register bundled real, openly-licensed benchmark assets (best-effort).
        from .benchmarks import load_benchmarks

        bench_dir = Path(__file__).resolve().parent / "data" / "benchmarks"
        n_bench = {"tasks": 0, "outputs": 0}
        if (bench_dir / "manifest.json").exists():
            try:
                n_bench = load_benchmarks(db, bench_dir / "manifest.json", bench_dir)
            except Exception as exc:  # noqa: BLE001 — seeding must not fail on a bad asset
                print(f"benchmark load skipped: {exc}")
```

Add `"benchmarks": n_bench` to the returned summary dict.

- [ ] **Step 2: Write the failing test (append to `tests/test_benchmarks.py`)**

```python
def test_seed_includes_real_benchmarks():
    summary = seed_all(force=True)
    assert summary["benchmarks"]["tasks"] >= 2
    with SessionLocal() as db:
        from app.models import ModelOutput
        import json as _json

        outs = db.query(ModelOutput).all()
        assert any(_json.loads(o.meta_json).get("benchmark") for o in outs)
```

- [ ] **Step 3: Run the FULL suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (prior 46 + new SDF/benchmark tests). If `test_research`/integrity counts shift because the seed now has more outputs, adjust those assertions to `>=` rather than `==` (do not weaken an integrity invariant — only loosen exact counts).

- [ ] **Step 4: Live-verify under uvicorn**

```bash
export BIO3D_DATA_DIR="$CLAUDE_JOB_DIR/tmp/sdf_verify"; rm -rf "$BIO3D_DATA_DIR"; mkdir -p "$BIO3D_DATA_DIR"
export BIO3D_ADMIN_TOKEN="live-token" BIO3D_GOLD_RATE="0"
.venv/bin/python -c "from app.seed import seed_all; print(seed_all(force=True))"
.venv/bin/python -m uvicorn app.main:app --port 8099 >"$CLAUDE_JOB_DIR/tmp/uv.log" 2>&1 &
sleep 3
# An SDF output is servable + a real benchmark is registered.
.venv/bin/python -c "
from app.database import SessionLocal; from app.models import ModelOutput, Task; import json
with SessionLocal() as db:
    sdf = db.query(ModelOutput).filter_by(asset_format='sdf').first()
    print('sdf output:', sdf.asset_path if sdf else None)
    bench = [o for o in db.query(ModelOutput).all() if json.loads(o.meta_json).get('benchmark')]
    print('benchmark outputs:', len(bench), [json.loads(b.meta_json).get('license') for b in bench][:3])
"
curl -s -o /dev/null -w 'sdf asset http=%{http_code}\n' "http://127.0.0.1:8099/assets/$(.venv/bin/python -c "from app.database import SessionLocal; from app.models import ModelOutput; db=SessionLocal(); print(db.query(ModelOutput).filter_by(asset_format='sdf').first().asset_path)")"
pkill -f 'uvicorn app.main:app' 2>/dev/null
```

Expected: an SDF asset path prints, serves 200; ≥2 benchmark outputs with `CC0` license.
**Caveat:** in-browser 3Dmol rendering of the SDF can't be confirmed without a headless browser — note this in the verification summary (becomes verifiable once the Increment-3 playwright prerequisite lands).

- [ ] **Step 5: Update `README.md`** — add SDF to the supported-formats list and a "Loading benchmark content" subsection documenting `scripts/fetch_benchmarks.py` and the manifest schema.

- [ ] **Step 6: Flip audit checkboxes** in `docs/audits/2026-06-20-field-audit.md`: C1 (SDF/docking display path partially unblocked), C3 "native SDF/MOL" → `[x]`. Leave voxel/point-cloud `[ ]`.

- [ ] **Step 7: Commit + merge**

```bash
git add app/seed.py README.md docs/audits/2026-06-20-field-audit.md tests/test_benchmarks.py
git commit -m "feat(seed): load bundled benchmarks; docs + audit status

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01N8JSB5JnrqT2N1hNUeYYoW"
git -C /home/mjarnold/bio3d-arena merge --ff-only worktree-bio3d-arena-mvp
```

---

## Self-Review (Increment 1)

- **Spec coverage:** C3-SDF (Tasks 1–3), C1 real content (Tasks 4–6), B5 provenance/license fields (Task 4 meta). Voxel/point-cloud/Gaussian-splat explicitly deferred to Increment 7. ✓
- **Type consistency:** `validate_asset` returns `subformat` (Task 1) consumed by no later task; `build_molecule_sdf` signature `(seed, out_path)->dict` matches seed call (Task 3) and benchmark fixture (Task 4); `load_benchmarks(db, manifest_path, assets_dir)->{"tasks","outputs","skipped"}` matches every caller (Tasks 4, 5, 6). ✓
- **Placeholders:** none — every step has runnable code/commands. ✓
- **Known fragility:** Task 5 depends on RCSB network; Step 1b gives a deterministic offline fallback so the increment always ships. Task 6 Step 3 may require loosening exact-count assertions in `test_research`/`test_integrity` (loosen counts only, never integrity invariants).
