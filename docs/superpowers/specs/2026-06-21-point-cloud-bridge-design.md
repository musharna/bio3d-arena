# Point-cloud → POINTS-GLB bridge for the spotlight — design

> Status: approved approach (brainstorm 2026-06-21). Fourth source increment for Subject
> Spotlight, after the mesh-only real-scan ingest. Mirror of `app/mesh_convert.py`.
> Decision locked: **faithful points** (render the raw scan as glTF POINTS), NOT surface
> reconstruction — honest to the spotlight's grounded-audit mission, no heavy dependency,
> no inferred surface layer.

## Goal

Put _real scanned plants that ship as point clouds_ into the spotlight beside the meshes and
artist models. The real plant-scan field is point-cloud-first (TomatoWUR / Crops3D / Pheno4D,
and likely Plant3D's skeleton architectures). The mesh-only v1 (`mesh_convert.to_glb`) skips
these by design. This increment renders them faithfully as glTF POINTS — the points ARE the
data; we add no inferred surface.

## Why this is small (extends, not rebuilds)

The scan pipeline already injects its converter:

```python
# scripts/source_scans.py:24 (LIVE)
def ingest_scans(db, mesh_paths, *, dataset, to_glb, score_fn=None, task_title=TOMATO_TITLE, limit=15)
```

So the bridge is: (1) a new converter that is the inverse of `mesh_convert.to_glb`; (2) one
new optional param so the grid can label a card "point cloud"; (3) the grid badge; (4) a
render + **independent-critic gate** confirming model-viewer actually shows the points.

## Decisions (locked)

- **Faithful points.** `trimesh.PointCloud(vertices, colors).export(file_type="glb")` writes
  glTF primitive **mode 0 (POINTS)** — VERIFIED (probe: 2 K pts → 24 KB GLB, round-trips).
- **Downsample cap.** Real clouds run to millions of points → browser-killing GLBs. Cap at a
  configurable `max_points` (default **200_000**) via a deterministic-seed random subsample so
  the GLB stays renderable and the run is reproducible. Log the original vs kept count.
- **Preserve vertex colours** when the source has them (most scans do) — colour is the only
  cue that reads as a plant when there is no surface/lighting.
- **Scoring stays isolated/best-effort** (existing discipline, `source_scans.py:58`). A POINTS
  GLB that the AgriGen scorer cannot chamfer stores `status=error`; the card still hosts. We do
  NOT touch the AgriGen scorer (read-only, one-writer-per-repo).
- **Render gate is mandatory.** model-viewer's point-size behaviour is undocumented; the
  increment is not done until a render + independent visual critic confirms the points are
  legible (not invisible 1px dust, not a shapeless smear). Fallback if illegible: raise default
  density (higher `max_points`) and/or a small custom-material tweak — NEVER switch to
  reconstruction (that was the rejected approach).

## Plan-time gate (FIRST task)

A real point-cloud file to exercise the path end-to-end. Two candidate sources, in order:

1. **Plant3D inner archive** (already downloaded, extracting): if its tomato architectures are
   point clouds / skeletons (README emphasises "network design principles" / "Steiner tree"),
   it IS the live points dataset — no extra download. Confirm format from the extracted listing.
2. Else **TomatoWUR** (CC-BY, 44 tomato) or **Pheno4D** (CC-BY, tomato + maize time series).
   Record which dataset exercised the real-execution check and why.
   If neither is in hand at build time, the code ships synthetic-tested (real `trimesh.PointCloud`
   fixtures) and the live ingest is a documented follow-up, exactly as the mesh path did.

## Components

### 1. `app/points_convert.py` — point cloud → POINTS-GLB (the format bridge)

Mirror of `mesh_convert.py`. Pure-ish (filesystem read only); trimesh already a dependency.

```python
class PointsConvertError(Exception): ...

def points_to_glb(src_path: str, *, max_points: int = 200_000, seed: int = 0) -> bytes:
    """Load a point-cloud asset (.ply/.pcd/.xyz/.obj-points) and export a glTF POINTS GLB.
    Raise PointsConvertError if it has NO vertices (empty / unreadable). If the source has
    faces (a real mesh), use its vertices as the point set (a mesh datum rendered as points
    is still faithful). Downsample to max_points with a fixed-seed RNG; preserve vertex
    colours when present."""
```

- Load with `trimesh.load(src_path)` (NOT `force="mesh"` — we want the cloud).
- Resolve a vertex array: `geom.vertices` (PointCloud or Trimesh both expose it). If `None` or
  `len == 0` → `PointsConvertError`.
- Resolve colours: `geom.colors` (PointCloud) or `geom.visual.vertex_colors` (Trimesh), else None.
- If `len(vertices) > max_points`: subsample indices with `np.random.RandomState(seed)` (apply
  the SAME indices to colours).
- `glb = trimesh.PointCloud(vertices, colors=colors).export(file_type="glb")`; raise if empty.
- Return bytes.

### 2. `scripts/source_scans.py` — one new param + a converter choice

- `ingest_scans(..., render_kind: str = "mesh")`: write `"render": render_kind` into the
  output meta (default `"mesh"` keeps existing scan cards unchanged). Everything else identical
  — the converter is still injected, depiction stays `whole_plant`, provenance + per-object
  commit + isolated scoring unchanged.
- The points converter raises `PointsConvertError` (a plain Exception, NOT `MeshConvertError`)
  so an empty/bad cloud counts as a real `errors` entry, not a silent `skipped_pointcloud`.
  (The `skipped_pointcloud` branch is for the MESH path meeting a cloud; on the POINTS path a
  cloud is the expected input, so there is nothing to skip.)
- `main()`: add `--render {mesh,points}` (default `mesh`). When `points`, wire
  `to_glb=points_to_glb` and `render_kind="points"`, and widen the file glob to cloud
  extensions (`*.ply *.pcd *.xyz` in addition to the mesh globs).

### 3. Spotlight grid — a "point cloud" badge

- `build_spotlight` (`app/spotlight.py:103`): read `render = meta.get("render", "mesh")` and add
  it to the model dict. No grouping change — point-cloud scans are still `cls=="scan"`,
  `depiction=="whole_plant"`, so they land in the existing "Real scan — whole plant" group.
- `app/templates/spotlight.html`: on a card with `render == "points"`, show a small
  "point cloud" badge so a viewer knows it is raw points, not a surface (honest labelling).

### 4. Render + independent-critic gate (real-execution)

- `scripts/render_spotlight.py` already screenshots each output via model-viewer — run it on the
  ingested points cards (no code change expected; it is format-agnostic).
- Then dispatch an **independent visual critic** (per the independent-critic-gate doctrine) on
  the rendered points thumbnail with a real-photo/mesh-scan reference in-frame: are the points
  legible as a plant, or invisible/over-dense? My own read is never the terminal gate.

## Data flow

```
scan.ply (points)  ──points_to_glb (trimesh, cap 200k, keep colour)──►  GLB (mode 0 POINTS)
   └─ ingest_scans(..., render_kind="points") ─► ModelOutput(source=<dataset>, license,
        attribution, external_url, meta.render="points", meta.depiction="whole_plant")
        ├─ score_and_store (isolated; hosts even if scorer rejects faces-less GLB)
        └─ render_spotlight ─► thumbnail ─► independent critic gate
/spotlight/tomato ─► "Real scan — whole plant" group, card shows a "point cloud" badge
```

## Error handling

- Empty / unreadable cloud → `PointsConvertError` → counted in `errors` (transparent).
- Per-object best-effort: one bad cloud never aborts the batch (existing `except`).
- Dataset dir absent → existing clear "download the dataset first" message, non-zero exit.

## Testing

- **Unit:** `points_to_glb` on a synthetic `trimesh.PointCloud` (real vertices+colours) →
  asserts the GLB JSON chunk carries primitive **mode 0**; downsample cap honoured
  (>max_points in → exactly max_points vertices out, deterministic for a fixed seed); colours
  preserved through subsample; raises `PointsConvertError` on a zero-vertex cloud. `ingest_scans`
  with injected `points_to_glb` + `render_kind="points"` registers a `scan:` ModelOutput whose
  meta has `render=="points"`; `build_spotlight` surfaces `render=="points"` on the model dict.
- **Real-execution (paired, data-gated):** convert ONE real cloud from the chosen dataset on
  disk → POINTS GLB → register against a temp DB; assert a `scan:` ModelOutput with a real
  on-disk POINTS GLB. Skips cleanly (never fake-passes) if no dataset is downloaded.
- **Render gate:** the independent-critic pass above is the visual real-execution check that a
  unit test cannot give.

## Out of scope (future increments)

- Surface reconstruction (the rejected approach) as an optional, separately-labelled "derived
  surface" card type.
- Normals-based / EDL point shading, octree LOD for very large clouds.
- Infinigen procedural generation + image-to-3D API generation (the next, separate increment).
