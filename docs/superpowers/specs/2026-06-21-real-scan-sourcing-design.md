# Real-scan dataset ingest for the spotlight — design

> Status: approved (brainstorm 2026-06-21). Third source increment for Subject Spotlight,
> after Objaverse found-models. Feeds the same /spotlight grid via the provenance schema.

## Goal

Put _real scanned tomato plants_ into the spotlight beside the AI reconstructions and the
artist-made fruit, so `/spotlight/tomato` becomes a genuine three-way audit:
**AI-recon attempts vs real scans vs artist models**. Objaverse supplied only fruit and zero
whole plants; academic scan datasets supply the missing whole-plant references.

## Decisions (locked at brainstorm)

- **First dataset = Plant3D** (Salk / Navlakha, Mendeley DOI 10.17632/9k7zctdyhs.1, CC-BY) —
  it ships `.obj` meshes (renderable after conversion), not just point clouds.
- **Dataset-agnostic architecture with a per-dataset adapter.** If Plant3D's tomato data is
  not usable (see the content/acquisition gate), the same adapter swaps to **TomatoWUR**
  (44 tomato, CC-BY) or **Crops3D** (83 tomato, CC-BY-NC-ND → host-internal under the
  existing private-tool policy). The architecture does not change.
- **"scan" is a distinct third source class** in the grid (not folded into "found").
- **Mesh-only in v1.** `<model-viewer>` renders GLB; meshes (`.obj`/`.ply`-with-faces) convert
  to GLB via trimesh. Point-cloud-only datasets (no faces) are DEFERRED to a future
  cloud→render bridge increment.
- **Whole-plant scans are scored** against the GT band (a sanity anchor — real plants should
  land within natural variation), with a **modality caveat** recorded (scan/microCT sensor
  differs from the GT bundle's sensor; ICP unit-bbox normalizes scale but not modality).
- **Cap** the number ingested to a representative sample (default ~15) so the grid stays
  scannable.

## Plan-time gate (FIRST task, before building the adapter)

Verify, with a small probe (not a full bulk download):

1. Plant3D is **downloadable** (Mendeley file listing / sizes manageable).
2. It actually contains **whole tomato-plant `.obj` meshes** (microCT can be seedlings/organs
   /fruit — confirm the tomato subset is whole plants with faces, not bare point clouds).
3. License is CC-BY as cataloged.
   If any fails, switch the v1 adapter to TomatoWUR (CC-BY) or Crops3D (NC-ND), whichever a quick
   probe confirms has renderable whole-tomato meshes. Record which dataset was chosen and why.

## Components

### 1. `app/sourcing.py` — source class + dataset registry (pure)

- `source_class(source: str | None) -> "ai" | "scan" | "found"`:
  `"bio3d-arena"` → `ai`; any slug in `SCAN_SOURCES` (`{"plant3d","tomatowur","crops3d","pheno4d"}`)
  → `scan`; everything else (objaverse, sketchfab, …) → `found`.
- `SCAN_DATASETS`: a registry mapping each scan-source slug → `{name, license, attribution,
url}` so provenance is consistent and the future cleanup can filter by it.

### 2. `app/mesh_convert.py` — mesh → GLB (the format bridge)

- `to_glb(src_path: str) -> bytes`: load with trimesh; if the geometry has **faces**, export
  GLB bytes; if it is a **point cloud** (no faces), raise `MeshConvertError("point-cloud,
deferred")` so the caller skips and counts it. Pure-ish (filesystem read only); trimesh is
  already a dependency (used by `ingest.validate_3d_asset`).

### 3. `scripts/source_scans.py` — the ingest pipeline

- `ingest_scans(db, mesh_paths, *, dataset, to_glb, score_fn=None, task_title=TOMATO_TITLE,
limit=15) -> dict`: for each candidate mesh (up to `limit`): convert to GLB (skip + count
  point-clouds / conversion failures), `register_output(..., generator_slug="scan:<dataset>",
generator_name=<dataset name>, data=<glb>, ext="glb", title=<scan id>,
meta={"depiction":"whole_plant","dataset":<slug>,"scan_id":…})`, then set provenance from
  the registry (`source=<slug>`, license, attribution, external_url) and `db.commit()` PER
  object (short write lock); if `score_fn and depiction=="whole_plant"`, score in an isolated
  try (a scoring failure never drops the hosted object — same discipline as the Objaverse
  pipeline). Returns `{"hosted","skipped_pointcloud","errors","by_depiction"}`.
- `main()` wires the chosen dataset adapter (locate the local dataset dir → glob the tomato
  `.obj`/`.ply` meshes), `to_glb`, and `recon_service.score_and_store`. The dataset download
  is a separate documented step (the plan's acquisition task), not done inside the app.

### 4. Spotlight grid — three-way grouping

- `build_spotlight` replaces the `found` boolean with `cls = source_class(o.source)` on each
  model dict (keep `found = cls != "ai"` for back-compat if referenced). Add `dataset` from
  meta for scan cards.
- The template groups by class: **"AI reconstruction"**, **"Real scan — whole plant"**
  (+ any other scan depiction), **"Found — fruit/leaf/other"**. Scan cards render like the
  rest (thumbnail + metrics + flags + provenance with license + DOI link).

### 5. Scoring + rendering — reuse

- Whole-plant scans → `recon_service.score_and_store` (already resolves the tomato species
  slug from the Task). Thumbnails → `scripts/render_spotlight.py` (operates per output).

## Data flow

```
Plant3D tomato .obj  ──to_glb (trimesh)──►  GLB bytes
   └─ register_output(scan:plant3d) ─► ModelOutput(source=plant3d, license=CC-BY,
        attribution=Salk + DOI, external_url=Mendeley, meta.depiction=whole_plant)
        ├─ score_and_store (whole_plant, isolated) ─► Metric vs GT band
        └─ render_spotlight ─► thumbnail
/spotlight/tomato ─► build_spotlight groups: AI recon | Real scan | Found(artist)
```

## Error handling

- Point-cloud mesh (no faces) → `MeshConvertError`, skipped, counted in `skipped_pointcloud`
  (transparent — not a silent drop).
- A conversion/register/scoring failure on one mesh logs + rolls back that object only and
  continues (best-effort batch).
- Dataset dir absent → the script reports a clear "download the dataset first" message and
  exits non-zero (no half state).

## Testing

- **Unit:** `source_class` over ai/scan/found inputs; `to_glb` converts a synthetic trimesh
  `.obj` (a real box mesh) → valid GLB bytes AND raises on a point-cloud `.ply` (no faces);
  `ingest_scans` with injected `to_glb`/fakes registers a `scan:` ModelOutput with correct
  provenance + depiction and counts a point-cloud skip; `build_spotlight` puts a scan output
  in the "scan" class.
- **Real-execution (paired, data-gated):** convert ONE real mesh from the chosen dataset (a
  file on disk) → GLB → register against a temp DB; assert a `scan:` ModelOutput with a real
  on-disk GLB and CC-BY provenance. Skips cleanly (never fake-passes) if the dataset is not
  downloaded.

## Out of scope (future increments)

- Point-cloud datasets (TomatoWUR/Crops3D/Pheno4D as clouds) — need a cloud→GLTF-POINTS or
  surface-reconstruction bridge.
- Infinigen procedural generation (folds into the api-generation increment).
- Direct found-repos (Sketchfab-CC, Smithsonian CC0).
