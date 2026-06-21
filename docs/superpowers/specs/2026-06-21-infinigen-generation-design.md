# Infinigen procedural generation for the spotlight — design

> Status: approved approach (brainstorm 2026-06-21). Sixth source increment for Subject
> Spotlight, after the image-to-3D API generation. Adds a PROCEDURAL generator class
> (Infinigen, BSD-3, local, no API key) — a generic bush first, tomato-tuning deferred.

## Goal

Add a procedural generator to the tomato spotlight: generate a plant with Infinigen
headlessly (CPU, no key), convert OBJ→GLB, and ingest it as a distinct **Procedural**
entry — scored and critic-gated like every other source. This is the only generator path
that produces real models with no API key, and it broadens the audit beyond commercial
image→3D APIs to rule-based procedural generation.

## Verified feasibility (research 2026-06-21, infinigen `main`)

- **Install (light path):** `conda create -n infinigen python=3.11` →
  `INFINIGEN_MINIMAL_INSTALL=True pip install -e .`. The Python-module mode bundles its own
  `bpy==4.2.0` wheel (the system Blender 4.5.10 is irrelevant to generation); minimal mode
  skips the terrain C-compile. HARD CONSTRAINT: **Python == 3.11** (fresh conda env).
- **Generation is CPU-only, no GPU/display** with `--render none` (geometry only).
- **Generator:** no tomato factory. **`BushFactory`** (stem + leaves) is the closest fit; the
  honest first entry is a _generic procedural bush_, NOT a tomato-specific model.
- **GLB:** Infinigen exports OBJ/FBX/STL/PLY/USDC, **not GLB natively** → convert the OBJ via
  the existing `app/mesh_convert.to_glb` bridge (trimesh load + decimate + GLB export) — the
  same path the laser scans already use. (Sources: infinigen docs/Installation.md,
  pyproject.toml, GeneratingIndividualAssets.md, ExportingToExternalFileFormats.md.)

## Why this fits the existing apparatus

- `register_output` (`app/ingest.py:172`) + per-object commit + provenance + isolated scoring
  is the same pattern the scan/objaverse/api adapters use.
- OBJ→GLB reuses `mesh_convert.to_glb(path, max_faces=...)` — no new converter.
- No schema changes: `source="infinigen"`, `meta_json` carries the factory + provenance.
- AgriGen stays read-only (we only consume its `/score` at `:8077`).

## Decisions (locked at brainstorm)

- **Distinct "Procedural" category** — `source_class("infinigen") == "procedural"`; a new
  spotlight group "Procedural (Infinigen)". NOT folded into AI reconstruction (rule-based ≠
  learned image→3D), scan, or found.
- **Generic `BushFactory` bush first** (YAGNI), honestly labeled. Tomato-tuning (compositing a
  `fruits` asset, parameter-tuning toward tomato habit, or authoring a tomato procedural à la
  the Infinigen flower.py gene→geometry template) is a deliberate FOLLOW-ON.
- **Infinigen runs in its own conda env**, invoked by the adapter via subprocess — it is NOT a
  dependency of the app's `.venv` (Python 3.11 pin + heavy install would pollute it).
- **Mesh output, decimated** like the scans (procedural meshes can be dense).

## Components

### 1. Infinigen env (operational setup, not app code)

Clone `princeton-vl/infinigen`, `conda create -n infinigen python=3.11`,
`INFINIGEN_MINIMAL_INSTALL=True pip install -e .`. Recorded in the ledger + a sidecar noting
the env name + commit. Heavy-ish install → submit via jobd if babysitting is wanted. This is a
controller operational step (like sourcing the API reference photo).

### 2. `source_class` → "procedural" + spotlight group

- `app/sourcing.py`: `source_class` returns `"procedural"` for `source == "infinigen"`
  (before the scan/found checks). Existing ai/scan/found behavior unchanged.
- `app/spotlight.py` `build_spotlight`: `cls` already comes from `source_class`, so an
  infinigen output gets `cls="procedural"` with no change there.
- `app/templates/spotlight.html`: add a **"Procedural (Infinigen)"** group
  (`scan | selectattr('cls','equalto','procedural')`-style) so procedural cards render in their
  own section.

### 3. `scripts/generate_infinigen.py` — the ingest adapter

- `ingest_infinigen(db, obj_paths, *, to_glb, score_fn=None, factory="BushFactory",
task_title=TOMATO_TITLE, limit=10) -> dict`: for each generated `.obj` (up to `limit`):
  `to_glb(path)` (skip on MeshConvertError, count it) → `register_output(generator_slug=
"infinigen", generator_name="Infinigen", data=glb, ext="glb", title=<asset id>,
meta={"depiction":"whole_plant","factory":factory,"render":"mesh"})` → set
  `out.source="infinigen"`, `out.license="BSD-3-Clause (Infinigen, Princeton VL)"`,
  `out.attribution="Infinigen procedural (BushFactory)"`,
  `out.external_url="https://github.com/princeton-vl/infinigen"` → `db.commit()` (hosted) →
  if `score_fn` and depiction whole_plant: isolated score (a scoring failure never drops the
  hosted object). Returns `{"hosted","skipped","errors","by_factory"}`.
- `main()`: shell out to the infinigen env to generate, then collect + ingest:
  - run `<infinigen_env_python> -m infinigen_examples.generate_individual_assets
--output_folder <tmp> -f <factory> -n <n> --render none --export obj` via `subprocess`
    (env python path or `conda run -n infinigen`), with a wall-clock timeout.
  - glob the produced `.obj` files under `<tmp>`, call `ingest_infinigen` with
    `to_glb=lambda p: mesh_convert.to_glb(p, max_faces=150_000)` and
    `recon_service.score_and_store`.
  - If the infinigen env / command is missing, print a clear "install the infinigen env first"
    message and exit non-zero (no half state).

### 4. Render + independent-critic gate (operational)

`scripts/render_spotlight.py` (gray-bg) → independent visual critic on the rendered bush:
does it read as a plant, are there decimation/topology defects, is the "Procedural" labeling
honest? My own read is never the terminal gate.

## Error handling

- Point-cloud / unconvertible OBJ → MeshConvertError, skipped + counted (transparent).
- Per-object best-effort; one bad asset never aborts the batch.
- Infinigen subprocess failure (non-zero exit / timeout) → clear error, non-zero exit, no
  partial ingest of a failed run.
- Infinigen env absent → "install the infinigen env first" message, exit non-zero.

## Testing

- **Unit (fixture OBJ, no Infinigen needed):** `source_class("infinigen") == "procedural"`;
  `ingest_infinigen` with a synthetic `.obj` (a real `trimesh.creation.box()` exported to OBJ)
  - injected `to_glb` registers a `ModelOutput` with `source="infinigen"`,
    `source_class → "procedural"`, BSD-3 provenance, `meta.factory`; `build_spotlight` puts it in
    the `procedural` class; the template renders a "Procedural (Infinigen)" group for a procedural
    card.
- **Real-execution (data-gated on the install):** run the real Infinigen generation in its env,
  convert ONE real bush OBJ → GLB → register against a temp DB; assert a `ModelOutput` with a
  real on-disk GLB. Skips cleanly if the infinigen env is not installed.
- **Render + critic gate:** the visual real-execution check a unit test cannot give.

## Out of scope (future increments)

- Tomato-tuning: compositing `fruits` assets, parameter-tuning BushFactory toward tomato habit,
  or authoring a tomato procedural model (the flower.py gene→geometry pattern).
- Other Infinigen factories (TreeFactory, FlowerFactory, fruits) as additional procedural entries.
- The live Tripo/API run (separate, key-gated) and more API providers.
