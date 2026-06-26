# Arabidopsis + Pine Coverage Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Raise benchmark subjects Arabidopsis thaliana (task id 10) and Pinus sylvestris (task id 13) from a single source each to multi-source parity with the covered four (tomato/maize/rose/soybean), using only sourceable non-imaging inputs.

**Architecture:** Each generator/source script already keys off a per-crop config dict + a `--crop`/`--species`/`--task` CLI selector and ingests via `app.ingest`, attaching outputs to a subject by exact task title and committing per object. This plan adds two crop keys (`arabidopsis`, `pinus`) plus their sourced inputs to each relevant script — the identical path maize/rose/soybean took. No new abstractions, no UI/ranking changes.

**Tech Stack:** Python 3 + SQLAlchemy + trimesh/PlantGL(L-Py); fal.ai + Replicate image→3D APIs; pytest (`.venv/bin/python -m pytest`, `pythonpath=["."]`, `testpaths=["tests"]`).

## Global Constraints

- **No new imaging.** Reference photos/assets come only from existing public/CC material (downloading a public CC photo is sourcing, not imaging).
- **Exact task titles** (must match the DB verbatim, em-dash `—` and `→`): Arabidopsis = `"Arabidopsis thaliana — single-image → 3D reconstruction"`; Pine = `"Pinus sylvestris — single-image → 3D reconstruction"`.
- **Honesty contract.** Deterministic sources (API recon, L-Py, PartCrafter) MUST land given keys+budget — a failure is a bug, not a silent skip. Best-effort sources (agrigen, Demeter, scans, Sketchfab, Objaverse) are sourced-or-skipped with a logged reason; NEVER substitute a generic, wrong-species, or mislabeled asset. Every output's `source`, `title`, species, and `license` must be truthful and mutually consistent (this is the rule the "rogue tomato" label incident established).
- **API keys** (`FAL_KEY`, `REPLICATE_API_TOKEN`) come from `~/.zshrc`; never log or paste them. Run live scripts as `source ~/.zshrc && .venv/bin/python scripts/<script>.py ...`.
- **Budget:** ~$12.5 of the $20 image-API budget remains; 7 API models × 2 subjects ≈ 14 calls fits.
- **Commit per source** (scripts self-commit per ingested object). Existing suite (68 tests) must stay green after every task.
- **Out of scope:** barley (id 18, volumetric root — deferred), #25 ground-truth/difficulty-tiers, #21 multi-view, any UI/ranking change.

---

## File Structure

- `data/assets/reference/arabidopsis_ref.jpg` + `arabidopsis_ref.json` — Arabidopsis CC reference photo + provenance (Task 1).
- `data/assets/reference/pinus_ref.jpg` + `pinus_ref.json` — Pine CC reference photo + provenance (Task 1).
- `scripts/generate_api_recon.py` — add title constants + `arabidopsis`/`pinus` `CROPS` entries (Task 2).
- `scripts/generate_partcrafter.py` — add title constants + `CROPS` entries (Task 3).
- `lpy/arabidopsis.lpy`, `lpy/pine.lpy` — new authored L-systems; `scripts/generate_lpy.py` — add constants + `CROPS` entries (Task 4).
- `scripts/source_scans.py` — add Arabidopsis ROMI scan dataset entry (Task 5).
- `scripts/generate_agrigen.py`, `scripts/generate_demeter.py`, `scripts/generate_sketchfab.py`, `scripts/source_objaverse.py` — best-effort entries (Task 6).
- `tests/test_*_crops.py` (or additions to existing `tests/test_generate_*.py`) — per-script CROPS-integrity tests.
- `docs/coverage/arabidopsis-pine-coverage.md` — final coverage table (Task 7).

A shared test helper for the data-integrity tests (Task 2 establishes it; later tasks reuse it):

```python
# tests/_coverage_helpers.py
import os

KNOWN_TITLES = {
    "Solanum lycopersicum — single-image → 3D reconstruction",
    "Zea mays — single-image → 3D reconstruction",
    "Rosa — single-image → 3D reconstruction",
    "Glycine max — single-image → 3D reconstruction",
    "Arabidopsis thaliana — single-image → 3D reconstruction",
    "Pinus sylvestris — single-image → 3D reconstruction",
}

def assert_crop_entry(entry, *, file_key=None):
    """Common checks for a per-crop config entry: known task title, and (if file_key given)
    that the referenced input file exists relative to repo root (cwd in the test run)."""
    assert entry["task_title"] in KNOWN_TITLES, entry["task_title"]
    if file_key is not None:
        assert os.path.exists(entry[file_key]), entry[file_key]
```

---

## Task 1: Source reference photos + provenance for Arabidopsis & Pine

**Files:**

- Create: `data/assets/reference/arabidopsis_ref.jpg`, `data/assets/reference/arabidopsis_ref.json`
- Create: `data/assets/reference/pinus_ref.jpg`, `data/assets/reference/pinus_ref.json`
- Test: `tests/test_reference_provenance.py`

**Interfaces:**

- Produces: two reference photos at the exact paths `data/assets/reference/{arabidopsis,pinus}_ref.jpg` (consumed by Tasks 2 and 3), each with a sibling `_ref.json` carrying required provenance keys.

**Sourcing requirements (no imaging):** find ONE clean, front-on, whole-plant CC or public-domain photo per subject on Wikimedia Commons or iNaturalist (prefer CC0 / CC-BY / CC-BY-SA). Arabidopsis: a potted rosette, ideally with a bolting inflorescence, on as plain a background as available. Pine: a young whole _Pinus sylvestris_ (sapling/seedling preferred over a forest scene). Download the original-resolution file. Record the real source URL, author, and license — do not invent them.

- [ ] **Step 1: Source and download the two photos**

Use web search / the Wikimedia/iNaturalist file pages to locate suitable CC images. Download with curl, e.g.:

```bash
cd /home/mjarnold/bio3d-arena/.claude/worktrees/bio3d-arena-mvp
curl -L -o data/assets/reference/arabidopsis_ref.jpg "<ARABIDOPSIS_DOWNLOAD_URL>"
curl -L -o data/assets/reference/pinus_ref.jpg "<PINE_DOWNLOAD_URL>"
file data/assets/reference/arabidopsis_ref.jpg data/assets/reference/pinus_ref.jpg
```

Expected: `file` reports both as JPEG image data. (If the chosen image is PNG, save as `.png` is NOT allowed — the pipeline expects `_ref.jpg`; convert with `.venv/bin/python -c "from PIL import Image; Image.open('src').convert('RGB').save('data/assets/reference/arabidopsis_ref.jpg','JPEG',quality=92)"`.)

- [ ] **Step 2: Write provenance sidecars**

Mirror the existing `tomato_ref.json` schema exactly. Fill EVERY field from the real source page (replace the bracketed values):

```json
{
  "subject": "Arabidopsis thaliana (whole plant, rosette + bolt)",
  "file": "arabidopsis_ref.jpg",
  "source": "[Wikimedia Commons | iNaturalist]",
  "source_url": "[page URL]",
  "download_url": "[direct file URL]",
  "license": "[CC0-1.0 | CC-BY-4.0 | CC-BY-SA-4.0]",
  "author": "[author]",
  "attribution": "[full attribution string incl. author, source, license]",
  "title": "[original file title]",
  "dimensions": "[WxH]",
  "note": "Canonical single-image input fed to every image-to-3D generator on the Arabidopsis spotlight. Sourced public CC photo (no imaging). License vetted against use (internal non-commercial research; /spotlight internal-only until pre-public re-vet). Swap by replacing this file + sidecar."
}
```

Repeat for `pinus_ref.json` with `"subject": "Pinus sylvestris (whole young tree)"` and `"file": "pinus_ref.jpg"`.

- [ ] **Step 3: Write the failing test**

```python
# tests/test_reference_provenance.py
import json, os
import pytest

REQUIRED = {"subject", "file", "source", "source_url", "download_url",
            "license", "author", "attribution", "title", "note"}
ALLOWED_LICENSES = {"CC0-1.0", "CC-BY-4.0", "CC-BY-SA-4.0", "CC-BY-3.0", "CC-BY-SA-3.0"}

@pytest.mark.parametrize("slug", ["arabidopsis", "pinus"])
def test_reference_has_image_and_valid_provenance(slug):
    img = f"data/assets/reference/{slug}_ref.jpg"
    meta = f"data/assets/reference/{slug}_ref.json"
    assert os.path.exists(img) and os.path.getsize(img) > 5000, img
    assert os.path.exists(meta), meta
    d = json.load(open(meta))
    assert REQUIRED <= set(d), REQUIRED - set(d)
    assert d["file"] == f"{slug}_ref.jpg"
    assert d["license"] in ALLOWED_LICENSES, d["license"]
    assert d["source_url"].startswith("http") and d["download_url"].startswith("http")
```

- [ ] **Step 4: Run the test**

Run: `.venv/bin/python -m pytest tests/test_reference_provenance.py -v`
Expected: PASS (both slugs).

- [ ] **Step 5: Commit**

```bash
git add data/assets/reference/arabidopsis_ref.* data/assets/reference/pinus_ref.* tests/test_reference_provenance.py
git commit -m "feat(refs): CC reference photos + provenance for Arabidopsis + Pine"
```

---

## Task 2: API recon (7 models × 2 subjects)

**Files:**

- Modify: `scripts/generate_api_recon.py` (title constants near line 21-22; `CROPS` dict lines 24-32; `--crop` choices already derive from `sorted(CROPS)` line 112-114 — no arg change needed)
- Create: `tests/_coverage_helpers.py` (the shared helper shown in File Structure)
- Create: `tests/test_crops_api_recon.py`

**Interfaces:**

- Consumes: `data/assets/reference/{arabidopsis,pinus}_ref.jpg` (Task 1).
- Produces: `CROPS["arabidopsis"]` and `CROPS["pinus"]` entries shaped `{"task_title": <TITLE>, "image": <path>}`, matching the existing entry shape.

- [ ] **Step 1: Create the shared helper file** — write `tests/_coverage_helpers.py` exactly as shown in the File Structure section above.

- [ ] **Step 2: Write the failing test**

```python
# tests/test_crops_api_recon.py
import pytest
from tests._coverage_helpers import assert_crop_entry
from scripts.generate_api_recon import CROPS

@pytest.mark.parametrize("crop,title", [
    ("arabidopsis", "Arabidopsis thaliana — single-image → 3D reconstruction"),
    ("pinus", "Pinus sylvestris — single-image → 3D reconstruction"),
])
def test_new_recon_crops_wired(crop, title):
    assert crop in CROPS, crop
    assert CROPS[crop]["task_title"] == title
    assert_crop_entry(CROPS[crop], file_key="image")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_crops_api_recon.py -v`
Expected: FAIL with `KeyError: 'arabidopsis'` (entries not added yet).

- [ ] **Step 4: Add the title constants and CROPS entries**

In `scripts/generate_api_recon.py`, after the existing `ROSE_TITLE = ...` constant add:

```python
ARABIDOPSIS_TITLE = "Arabidopsis thaliana — single-image → 3D reconstruction"
PINE_TITLE = "Pinus sylvestris — single-image → 3D reconstruction"
```

Then extend the `CROPS` dict (after the `soybean` entry) with:

```python
    "arabidopsis": {"task_title": ARABIDOPSIS_TITLE, "image": "data/assets/reference/arabidopsis_ref.jpg"},
    "pinus": {"task_title": PINE_TITLE, "image": "data/assets/reference/pinus_ref.jpg"},
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_crops_api_recon.py tests/test_generate_api_recon.py -v`
Expected: PASS (new wiring test + existing recon tests unaffected).

- [ ] **Step 6: Commit the wiring**

```bash
git add scripts/generate_api_recon.py tests/_coverage_helpers.py tests/test_crops_api_recon.py
git commit -m "feat(recon): wire Arabidopsis + Pine into API recon CROPS"
```

- [ ] **Step 7: Live run — 7 models per subject** (deterministic; must land)

```bash
source ~/.zshrc
.venv/bin/python scripts/generate_api_recon.py --crop arabidopsis
.venv/bin/python scripts/generate_api_recon.py --crop pinus
```

Expected: each run reports `generated` > 0 and `skipped_no_key` only for providers whose key is absent. If a provider with a present key errors, fix the cause (do not skip silently) — consult the prior bake-off bug notes in memory `recon_bakeoff_2026-06-23` (6 live-API bugs already fixed in `app/image3d.py`).

- [ ] **Step 8: Real-execution verification**

```bash
sqlite3 data/arena.db "SELECT t.id, mo.source, COUNT(*) FROM task t JOIN model_output mo ON mo.task_id=t.id WHERE t.id IN (10,13) AND mo.source LIKE 'api:%' GROUP BY t.id, mo.source;"
```

Expected: `api:*` rows present for BOTH task 10 and task 13. Spot-check one asset file exists: `ls -la $(sqlite3 data/arena.db "SELECT asset_path FROM model_output WHERE task_id=10 AND source LIKE 'api:%' LIMIT 1;")`. (The script self-commits per object; no extra commit needed.)

---

## Task 3: PartCrafter (image-based frontier, both subjects)

**Files:**

- Modify: `scripts/generate_partcrafter.py` (title constants; `CROPS` dict lines 34-55)
- Create: `tests/test_crops_partcrafter.py`

**Interfaces:**

- Consumes: the two reference photos (Task 1).
- Produces: `CROPS["arabidopsis"]` / `CROPS["pinus"]` shaped `{"task_title", "image", "tag"}` (note the extra `tag` key vs recon).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_crops_partcrafter.py
import pytest
from tests._coverage_helpers import assert_crop_entry
from scripts.generate_partcrafter import CROPS

@pytest.mark.parametrize("crop,title,tag", [
    ("arabidopsis", "Arabidopsis thaliana — single-image → 3D reconstruction", "arabidopsis"),
    ("pinus", "Pinus sylvestris — single-image → 3D reconstruction", "pinus"),
])
def test_new_partcrafter_crops_wired(crop, title, tag):
    assert crop in CROPS
    assert CROPS[crop]["task_title"] == title
    assert CROPS[crop]["tag"] == tag
    assert_crop_entry(CROPS[crop], file_key="image")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_crops_partcrafter.py -v`
Expected: FAIL with `KeyError: 'arabidopsis'`.

- [ ] **Step 3: Add constants + CROPS entries**

In `scripts/generate_partcrafter.py`, add the two title constants (same two lines as Task 2 Step 4) after the existing title constants, then extend `CROPS` after the `soybean` entry:

```python
    "arabidopsis": {
        "task_title": ARABIDOPSIS_TITLE,
        "image": "data/assets/reference/arabidopsis_ref.jpg",
        "tag": "arabidopsis",
    },
    "pinus": {
        "task_title": PINE_TITLE,
        "image": "data/assets/reference/pinus_ref.jpg",
        "tag": "pinus",
    },
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_crops_partcrafter.py tests/test_generate_partcrafter.py -v`
Expected: PASS.

- [ ] **Step 5: Commit wiring**

```bash
git add scripts/generate_partcrafter.py tests/test_crops_partcrafter.py
git commit -m "feat(partcrafter): wire Arabidopsis + Pine into CROPS"
```

- [ ] **Step 6: Live run** (deterministic; must land)

```bash
source ~/.zshrc
.venv/bin/python scripts/generate_partcrafter.py --crop arabidopsis
.venv/bin/python scripts/generate_partcrafter.py --crop pinus
```

Expected: a `frontier:partcrafter` output ingested for each. If PartCrafter is self-hosted/GPU-backed and unavailable in this environment, that is a BLOCKER (deterministic source) — report it (don't skip); the controller decides whether to defer PartCrafter or run it on a GPU host via jobd.

- [ ] **Step 7: Real-execution verification**

```bash
sqlite3 data/arena.db "SELECT t.id, COUNT(*) FROM task t JOIN model_output mo ON mo.task_id=t.id WHERE t.id IN (10,13) AND mo.source='frontier:partcrafter' GROUP BY t.id;"
```

Expected: a row for both task 10 and 13.

---

## Task 4: L-Py authored systems (Arabidopsis rosette+bolt, Pine conifer)

**Files:**

- Create: `lpy/arabidopsis.lpy`, `lpy/pine.lpy`
- Modify: `scripts/generate_lpy.py` (constants near line 23-25; `CROPS` dict lines 30-34)
- Create: `tests/test_crops_lpy.py`

**Interfaces:**

- Produces: `CROPS["arabidopsis"]` / `CROPS["pinus"]` shaped `{"model": <lpy path>, "task_title": <TITLE>, "variant": <name>}`.

**Acceptance (critic gate, per the L-Py convention in `scripts/generate_lpy.py` docstring):** the generated mesh must read morphologically as the species — Arabidopsis: a flat basal rosette of leaves + a thin erect bolting stem (NOT a bushy tomato-like form); Pine: a single central trunk with whorled lateral branches bearing needle tufts (excurrent/conical, NOT a rounded bush). If the starter L-system below does not pass this gate when rendered, refine it in the `lpy` conda env until it does — the starters are runnable templates modeled on `lpy/soybean.lpy`, not frozen.

- [ ] **Step 1: Write the Arabidopsis L-system** — create `lpy/arabidopsis.lpy`:

```python
import openalea.plantgl.all as pgl
from math import pi

# Authored Arabidopsis thaliana (thale cress) L-system for bio3d-arena.
# Rosette dicot: a flat basal ROSETTE of spatulate leaves at ground level, then a thin erect
# BOLTING inflorescence bearing small cauline leaves and a terminal cluster of tiny 4-petal
# flowers + slender siliques. The flat rosette + thin bolt are the Arabidopsis hallmarks.


def _spatulate(length, width):
    """Obovate rosette leaf: narrow base, broadest toward the rounded tip. Midrib +X, blade XY."""
    n = 12
    pts = [pgl.Vector3(0.0, 0.0, 0.0)]
    for i in range(1, n + 1):
        t = i / n
        x = length * t
        w = width * max(0.0, (1.0 - abs(t - 0.70) / 0.95)) ** 0.55
        pts.append(pgl.Vector3(x, w, 0.0))
        pts.append(pgl.Vector3(x, -w, 0.0))
    idx = [pgl.Index3(0, 1, 2)]
    for i in range(1, n):
        l0, r0 = 2 * i - 1, 2 * i
        l1, r1 = 2 * i + 1, 2 * i + 2
        idx += [pgl.Index3(l0, l1, r1), pgl.Index3(l0, r1, r0)]
    ts = pgl.TriangleSet(pts, idx)
    ts.computeNormalList()
    return ts


def flower(scale=1.0):
    """Tiny 4-petal cruciform Arabidopsis flower."""
    shapes = []
    for k in range(4):
        pet = pgl.Translated(0.35 * scale, 0, 0, pgl.Sphere(0.16 * scale, slices=5, stacks=4))
        shapes.append(pgl.AxisRotated((1, 0, 0), k * pi / 2, pet))
    return pgl.Group(shapes)


def silique(scale=1.0):
    """Slender erect seed pod (the inflorescence fruit)."""
    return pgl.AxisRotated((0, 1, 0), -pi / 2.6, pgl.Cylinder(0.10 * scale, 1.6 * scale, slices=5))


module Rosette
module Bolt(n)

# +H = +Z up. ,(c): 2→leaf, 3→silique, 4→flower, 6→bolt stem.
Axiom: ,(6) Rosette Bolt(7)

derivation length: 8
production:

Rosette:
  # ~9 spatulate leaves splayed nearly horizontal in a 137.5° spiral at ground level
  nproduce [
  for i in range(9):
    nproduce /(137.5) [ &(80) ,(2) /(90) @g(_spatulate(3.0 + 0.15 * i, 1.4)) ]
  produce ]

Bolt(n):
  if n <= 0:
    produce [ ,(4) @g(flower(1.0)) ] [ &(20) ,(3) @g(silique(1.0)) ]
  else:
    nproduce ,(6) _(0.14) F(3.2) /(137.5)
    if n >= 5:
      nproduce [ &(55) ,(2) /(90) @g(_spatulate(1.6, 0.6)) ]
    if n <= 4:
      nproduce [ +(35) ,(4) @g(flower(0.8)) ] [ -(30) &(25) ,(3) @g(silique(0.9)) ]
    produce Bolt(n - 1)
```

- [ ] **Step 2: Write the Pine L-system** — create `lpy/pine.lpy`:

```python
import openalea.plantgl.all as pgl
from math import pi

# Authored Pinus sylvestris (Scots pine) L-system for bio3d-arena.
# Conifer: a straight central woody TRUNK with WHORLS of lateral branches (excurrent/conical),
# each branch tipped and lined with FASCICLES of paired blue-green needles. Whorled branching +
# needle fascicles + conical taper (lower whorls longer) are the pine hallmarks.


def needle_fascicle(scale=1.0):
    """A tuft of ~6 thin needles splaying from a point."""
    shapes = []
    for k in range(6):
        a = k * (2 * pi / 6)
        needle = pgl.Cylinder(0.04 * scale, 2.4 * scale, slices=4)
        shapes.append(pgl.AxisRotated((0, 0, 1), a, pgl.AxisRotated((0, 1, 0), 0.5, needle)))
    return pgl.Group(shapes)


def branch_with_needles(length, scale=1.0):
    """A woody lateral branch bearing needle fascicles along its length and a tuft at the tip."""
    shapes = [pgl.AxisRotated((0, 1, 0), pi / 2, pgl.Cylinder(0.10 * scale, length, slices=5))]
    m = 4
    for i in range(1, m + 1):
        x = length * i / (m + 1)
        shapes.append(pgl.Translated(x, 0, 0, needle_fascicle(0.8 * scale)))
    shapes.append(pgl.Translated(length, 0, 0, needle_fascicle(1.0 * scale)))
    return pgl.Group(shapes)


module Trunk(n)

# +H = +Z up the trunk. ,(7)→bark. A whorl of 5 branches per node; branch length tapers upward.
Axiom: ,(7) _(0.6) Trunk(7)

derivation length: 8
production:

Trunk(n):
  if n <= 0:
    produce [ &(10) @g(needle_fascicle(1.1)) ]
  else:
    nproduce ,(7) _(0.5 * (0.4 + n / 9.0)) F(5.0)
    blen = 5.0 + 1.4 * n
    pitch = 60 + 3 * (7 - n)
    for k in range(5):
      nproduce [ /(72.0 * k + 17.0 * n) &(pitch) @g(branch_with_needles(blen, 1.0)) ]
    produce Trunk(n - 1)
```

- [ ] **Step 3: Write the failing wiring test**

```python
# tests/test_crops_lpy.py
import pytest
from tests._coverage_helpers import assert_crop_entry
from scripts.generate_lpy import CROPS

@pytest.mark.parametrize("crop,model,title", [
    ("arabidopsis", "lpy/arabidopsis.lpy", "Arabidopsis thaliana — single-image → 3D reconstruction"),
    ("pinus", "lpy/pine.lpy", "Pinus sylvestris — single-image → 3D reconstruction"),
])
def test_new_lpy_crops_wired(crop, model, title):
    assert crop in CROPS
    assert CROPS[crop]["model"] == model
    assert CROPS[crop]["variant"] == crop
    assert_crop_entry(CROPS[crop])  # title check; model-file existence checked by Step 4 run
    import os
    assert os.path.exists(model), model
```

- [ ] **Step 4: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_crops_lpy.py -v`
Expected: FAIL with `KeyError: 'arabidopsis'`.

- [ ] **Step 5: Add constants + CROPS entries**

In `scripts/generate_lpy.py`, after `SOYBEAN_TITLE = ...` add the two title constants (same strings as before), then extend `CROPS`:

```python
    "arabidopsis": {"model": "lpy/arabidopsis.lpy", "task_title": ARABIDOPSIS_TITLE, "variant": "arabidopsis"},
    "pinus": {"model": "lpy/pine.lpy", "task_title": PINE_TITLE, "variant": "pinus"},
```

- [ ] **Step 6: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_crops_lpy.py tests/test_generate_lpy.py -v`
Expected: PASS.

- [ ] **Step 7: Generate the meshes in the lpy env + critic gate**

```bash
.venv/bin/python scripts/generate_lpy.py --crop arabidopsis
.venv/bin/python scripts/generate_lpy.py --crop pinus
```

Expected: each subprocesses the `lpy` conda env, runs `lpy_runner.py` on the new `.lpy`, converts OBJ→GLB, and ingests a `procedural:lpy` output. If L-Py raises a syntax/runtime error, fix the `.lpy` (it is an authored template — iterate). Then apply the critic gate: render/inspect the GLB and confirm the morphology described in Acceptance. If morphology fails (e.g. rosette looks bushy, pine looks like a shrub), refine the L-system and regenerate before proceeding.

- [ ] **Step 8: Commit**

```bash
git add lpy/arabidopsis.lpy lpy/pine.lpy scripts/generate_lpy.py tests/test_crops_lpy.py
git commit -m "feat(lpy): authored Arabidopsis (rosette+bolt) + Pine (conifer) L-systems"
```

(The generate run self-commits the ingested GLB outputs separately.)

---

## Task 5: ROMI Arabidopsis real scan import

**Files:**

- Modify: `scripts/source_scans.py` (`SCAN_TASKS` dict lines 25-30; the dataset metadata the script reads via `meta_d` — match the existing dataset-metadata pattern the script already uses for tomato/maize scans)
- Create: `tests/test_crops_scans.py`

**Interfaces:**

- Consumes: agrigen's on-disk ROMI Arabidopsis asset (a real space-carved scan; see memory `hunyuan_reconciliation_2026-06-26` for ROMI refs — ROMI Zenodo 10379172). Locate the mesh/point-cloud under `/home/mjarnold/agrigen/backend/data/romi_realplant/` or adjacent.
- Produces: `SCAN_TASKS["arabidopsis"]` entry + an ingested `scan:*` (or the script's dataset slug) output attached to task 10, with truthful ROMI attribution/license.

**Note:** this is best-effort but high-value and confirmed available. If the ROMI asset is a point cloud rather than a mesh, convert it to a viewable GLB (e.g. Poisson/ball-pivot surface reconstruction or a point-cloud GLB the viewer registry supports) — record the conversion in the output's `meta_json`. Do NOT relabel it as anything other than a real ROMI Arabidopsis scan.

- [ ] **Step 1: Locate the ROMI Arabidopsis asset**

```bash
ls -R /home/mjarnold/agrigen/backend/data/romi_realplant/ 2>/dev/null | head -50
find /home/mjarnold/agrigen -iname '*.ply' -o -iname '*.obj' 2>/dev/null | grep -i romi | head
```

Expected: a point cloud / mesh file for the ROMI Arabidopsis plant. Record its path and the real ROMI license (CC-BY per Zenodo 10379172 — verify on the Zenodo record).

- [ ] **Step 2: Write the failing wiring test**

```python
# tests/test_crops_scans.py
from scripts.source_scans import SCAN_TASKS

def test_arabidopsis_scan_task_registered():
    assert "arabidopsis" in SCAN_TASKS
    assert SCAN_TASKS["arabidopsis"] == "Arabidopsis thaliana — single-image → 3D reconstruction"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_crops_scans.py -v`
Expected: FAIL with `KeyError: 'arabidopsis'`.

- [ ] **Step 4: Register the scan task + dataset metadata**

In `scripts/source_scans.py`, add `ARABIDOPSIS_TITLE` constant and extend `SCAN_TASKS`:

```python
    "arabidopsis": ARABIDOPSIS_TITLE,
```

Add the ROMI dataset to the script's dataset-metadata structure (match the existing pattern used for the tomato/maize datasets — same keys: `license`, `attribution`, `url`), with verified ROMI values:

```python
# dataset slug e.g. "scan:romi-arabidopsis"
{"license": "CC-BY-4.0", "attribution": "ROMI Arabidopsis thaliana scan, Zenodo 10379172", "url": "https://zenodo.org/records/10379172"}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_crops_scans.py tests/test_source_scans.py -v`
Expected: PASS.

- [ ] **Step 6: Import + ingest the scan**

Run the script against the located ROMI mesh/cloud (use the script's actual `--task`/`--dataset`/path args as defined in `source_scans.py`):

```bash
.venv/bin/python scripts/source_scans.py --task arabidopsis --dataset <romi-slug> <path-to-romi-mesh-or-converted-glb>
```

Expected: one real-scan output ingested for task 10.

- [ ] **Step 7: Real-execution verification + commit**

```bash
sqlite3 data/arena.db "SELECT source, license FROM model_output WHERE task_id=10 AND source LIKE '%romi%' OR (task_id=10 AND source LIKE 'scan%');"
git add scripts/source_scans.py tests/test_crops_scans.py
git commit -m "feat(scans): import ROMI Arabidopsis real scan as a coverage source"
```

Expected: a row with the truthful ROMI attribution/license. (Ingested asset self-committed.)

---

## Task 6: Best-effort sources (agrigen, Demeter, Sketchfab, Objaverse, Pine scan)

**Files:**

- Modify (only where the species is genuinely supported): `scripts/generate_agrigen.py`, `scripts/generate_demeter.py`, `scripts/generate_sketchfab.py`, `scripts/source_objaverse.py`
- Create: `docs/coverage/best-effort-log.md`

**Interfaces:** each source is attempted; if the generator/library does not support the species or no CC asset exists, record a one-line reason in `docs/coverage/best-effort-log.md` and move on. No fabricated/mislabeled assets (Global Constraints honesty contract).

- [ ] **Step 1: agrigen** — check whether an Arabidopsis/Pine plant descriptor exists in the agrigen repo (`grep -ri 'arabidopsis\|pinus\|pine' /home/mjarnold/agrigen --include=*.py -l`). If a usable procedural descriptor exists, add the crop to `AGRIGEN_CROPS` (shape `{"variant", "task_title", "attribution", "caveat"}`) and run `scripts/generate_agrigen.py --crop <crop>`. If NOT (expected: agrigen has only tomato/maize/rose), log: `agrigen: skipped <crop> — no plant descriptor in agrigen repo` and make no code change.

- [ ] **Step 2: Demeter** — determine whether Demeter models the species (`--species` support). If yes, add to `SPECIES_TASKS` and run. If not (expected: Pine unsupported; Arabidopsis likely unsupported), log the reason. No mislabeled output.

- [ ] **Step 3: Sketchfab found assets** — search Sketchfab for downloadable CC models: Pine (`Pinus sylvestris`, Scots pine, conifer) — expected to exist; Arabidopsis (`Arabidopsis thaliana`, thale cress) — expected rare. For each real CC asset found, add it to the script's per-crop `ASSETS` list (shape per existing entries: `variant, uid, name, author, license, keep`) with its TRUE uid/author/license, add the crop to `CROPS` (`{"task_title", "assets"}`), and run `scripts/generate_sketchfab.py --crop <crop>`. Log any subject with zero suitable CC assets.

- [ ] **Step 4: Objaverse** — add `arabidopsis`/`pinus` entries to `source_objaverse.py` `CROPS` (full shape: `task_title, lvis_keyword, name_includes, name_excludes, require_public_safe, depiction_override`) using appropriate LVIS keywords/filters, and run `scripts/source_objaverse.py --crop <crop>`. If the filtered query yields no plausible plant asset, log it (Objaverse has many false positives — keep `name_excludes` strict; better to skip than mislabel).

- [ ] **Step 5: Pine real scan** — quick search for a public CC conifer/pine 3D scan dataset. If a clearly-licensed one exists, register it in `source_scans.py` `SCAN_TASKS["pinus"]` + dataset metadata and import (mirror Task 5). If none found, log: `pine scan: skipped — no public CC conifer scan dataset located`.

- [ ] **Step 6: Run the full suite + commit code + log**

Run: `.venv/bin/python -m pytest -q`
Expected: all green.

```bash
git add scripts/generate_agrigen.py scripts/generate_demeter.py scripts/generate_sketchfab.py scripts/source_objaverse.py docs/coverage/best-effort-log.md 2>/dev/null
git commit -m "feat(coverage): best-effort agrigen/Demeter/Sketchfab/Objaverse/pine-scan for Arabidopsis + Pine (skip-and-log where unsupported)"
```

(Only stage files that actually changed. Ingested assets self-commit.)

---

## Task 7: Coverage table, verification, memory, merge prep

**Files:**

- Create: `docs/coverage/arabidopsis-pine-coverage.md`
- Update: `~/.claude/projects/-home-mjarnold-bio3d-arena/memory/` (new memory file + `MEMORY.md` pointer)

- [ ] **Step 1: Produce the final coverage table**

```bash
sqlite3 -header -column data/arena.db "SELECT t.id, t.title, COUNT(mo.id) outputs, COUNT(DISTINCT mo.source) n_src FROM task t LEFT JOIN model_output mo ON mo.task_id=t.id WHERE t.id IN (10,13) GROUP BY t.id;"
sqlite3 -header -column data/arena.db "SELECT t.id, mo.source, COUNT(*) n FROM task t JOIN model_output mo ON mo.task_id=t.id WHERE t.id IN (10,13) GROUP BY t.id, mo.source ORDER BY t.id, n DESC;"
```

Write `docs/coverage/arabidopsis-pine-coverage.md` with: the per-subject × per-source table (filled counts), and the best-effort skip log (what was unavailable and why). Confirm each subject has ≥9 distinct sources (the deterministic floor) — if not, a deterministic source silently failed; investigate before declaring done.

- [ ] **Step 2: Real-execution viewer check**

Boot the app and confirm both subjects' new outputs load:

```bash
source ~/.zshrc && .venv/bin/python -m uvicorn app.main:app --port 8011 &
sleep 4
curl -s "http://127.0.0.1:8011/arena?task_id=10" -o /dev/null -w "%{http_code}\n"
curl -s "http://127.0.0.1:8011/arena?task_id=13" -o /dev/null -w "%{http_code}\n"
kill %1
```

Expected: HTTP 200 for both; the arena can now serve a pair for tasks 10 and 13.

- [ ] **Step 3: Full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all green (original 68 + new tests).

- [ ] **Step 4: Record to memory**

Write a project memory file (e.g. `arabidopsis_pine_coverage_2026-06-26.md`) summarizing: scope (gap-fill, full parity, no imaging), final per-source coverage counts for tasks 10/13, which best-effort sources filled vs skipped (+reasons), and the spec/plan paths. Add a one-line pointer to `MEMORY.md`. (Memory dir is the canonical project slug `-home-mjarnold-bio3d-arena`, not the worktree slug — write with `IRON_LAW_OVERRIDE=1` if cwd is the worktree.)

- [ ] **Step 5: Commit + finish branch**

```bash
git add docs/coverage/arabidopsis-pine-coverage.md
git commit -m "docs(coverage): Arabidopsis + Pine parity coverage table + report"
```

Then use superpowers:finishing-a-development-branch to merge to master (ff-merge, the project convention).

---

## Self-Review

**Spec coverage:** §1 gap → all tasks; §2 non-goals (barley/imaging) → Global Constraints; §3 architecture (CROPS reuse) → every task; §4 source matrix → Task 2 (API), 3 (PartCrafter), 4 (L-Py), 5 (ROMI scan), 6 (best-effort agrigen/Demeter/Sketchfab/Objaverse/pine-scan); §5 inputs sourcing → Task 1 + Task 5; §6 honesty contract → Global Constraints + Task 6 skip-and-log; §7 budget → Global Constraints; §8 testing (unit + real-execution) → each task's wiring test + DB/viewer checks; §9 deliverable → Task 7. No gaps.

**Placeholder scan:** Bracketed values in Task 1 (`<URL>`, license) and Task 5 (ROMI path/slug) are genuine runtime-sourced values the implementer must fill from real sources — they are explicitly framed as "fill from the real source page," not lazy TODOs. All code blocks are complete.

**Type consistency:** `CROPS` entry shapes differ by script and each task uses the correct shape — recon `{task_title,image}`, partcrafter `{task_title,image,tag}`, lpy `{model,task_title,variant}`, scans `SCAN_TASKS` title-map, agrigen `{variant,task_title,attribution,caveat}`, objaverse full filter dict. Title constants `ARABIDOPSIS_TITLE`/`PINE_TITLE` use identical strings across all scripts. Shared helper `assert_crop_entry` signature consistent across Tasks 2/3/4.
