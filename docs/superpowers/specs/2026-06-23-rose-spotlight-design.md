# Rose (Rosa) subject spotlight — design (Track A3)

**Date:** 2026-06-23
**Status:** approved (scoping + tiered plan) → per-tier plans + SDD execution
**Scope:** the 3rd crop spotlight in bio3d-arena (after tomato, maize), covering Rosa across source classes.
**Scoping source:** `~/.claude/projects/-home-mjarnold-bio3d-arena/memory/rose_coverage_scoping_2026-06-23.md` (3-scout verified, licenses live-checked).
**Builds on:** the crop-parametric infra from the maize arc (`--crop`/`--task` selectors on source_scans, source_volumetric, generate_sketchfab, generate_partcrafter, generate_agrigen, generate_demeter) + the maize spotlight pattern.

## Motivation

Rose is Track A3 — the next crop spotlight. Unlike maize, rose has a **real CC0 3D dataset (ROSE-X X-ray CT)**, a clean CC0
reference photo, and strong found assets; the weak class is procedural (flower bloom is hard universally). This brings rose
across the arena's source classes, reusing the crop-parametric scripts built during the maize arc.

## Subject

- New recon subject Task: **`Rosa — single-image → 3D reconstruction`** (under the `plants` category), the home all rose
  outputs attach to (mirrors the maize/tomato recon subjects).
- New spotlight entry: slug **`rose`**, featured, with the CC0 _Rosa canina_ reference photo.
- Reference photo: CC0 _Rosa canina_ bush-in-bloom (Wikimedia, Cultureel Gelderland) → `data/assets/reference/rose_ref.jpg` + a
  `rose_ref.json` credit sidecar (mirrors maize_ref). Gates AI recon + PartCrafter.
- Recon Mode-B GT: defer scoring initially (rose has no recon GT bundle); ROSE-X could later serve as rose GT. Outputs host unscored.

## Tier 1 — ROSE-X real scan + volumetric (CC0, highest value)

- **Dataset:** ROSE-X — real _Rosa rugosa_ X-ray CT, 11 potted plants, CC0 (PMC7057657). Download: ROSE-X.zip on U.Angers
  Nextcloud `https://uabox.univ-angers.fr/index.php/s/rnPm5EHFK6Xym9t/download` (GET; HEAD blocked; size unconfirmed — probe first).
- **scan class:** ingest ROSE-X PLY point clouds as `source='rose-x'` via the existing `source_scans.py` points path
  (`points_convert`, `--task rose --render points`). New `SCAN_DATASETS['rose-x']` provenance entry (CC0).
- **volumetric class:** ingest the ROSE-X CT volume as `source='ct:rose-x'` via `source_volumetric.py` (`volume_to_glb`). New
  `VOLUMETRIC_DATASETS['rose-x']` (CC0, modality CT). **Format risk:** ROSE-X volume = image stacks (TIFF or raw); `volume_to_glb`
  reads `.nii`/`.tif`. If TIFF-stack → works as-is; if raw/other → a data-prep conversion step (like the e!DAL Analyze→nii) or a
  small reader add. Confirm format on download.
- **Acquisition guard:** if ROSE-X is inaccessible or impractically large, STOP and report; do not silently substitute. Fallback for
  the _scan_ class only: the CC0 Sketchfab rose photogrammetry scans (Tier 2 set) can stand in, clearly labeled.

## Tier 2 — found:sketchfab + AI recon + frontier

- **found:sketchfab:** add a `rose` entry to `generate_sketchfab.py` CROPS with ~4 curated verified roses. Prefer the
  gallery-weight CC-BY/CC0 ones (Rosa chinensis `139cd6e4...` 8MB, giantbooley bush `a92d6bdc...`, RosticOstafi `3f97dc47...`,
  CC0 bloom `5bb9112f...`). The heavy CC0 botanical whole-plants (`696091d2...` 88MB, `78127e37...` 97MB) need decimation — add a
  decimate pass to the sketchfab Blender convert (reuse the partcrafter/xfrog `max_faces` idiom) OR keep them out of the curated set.
- **AI recon:** run the recon pipeline (TRELLIS/Hunyuan3D/InstantMesh) on `rose_ref.jpg` → `source='bio3d-arena'` outputs on the
  rose subject (mirrors how tomato/maize got their recon entries). GPU-gated via jobd.
- **frontier:partcrafter:** add a `rose` entry to `generate_partcrafter.py` CROPS (image=rose_ref.jpg, tag/variant=rose) → run
  self-hosted on the laptop 5090 via jobd (`--host laptop --gpu vram_gb=12`).

## Tier 3 — procedural (weak on bloom, honest caveats)

- **procedural:infinigen (strongest):** integrate Infinigen `FlowerFactory` (BSD-3, ~/infinigen) → export a bloom GLB → ingest
  `source='procedural:infinigen'`. Caveat: generic parameterized flower, not unambiguously rose (no rose preset). New generator wiring.
- **procedural:agrigen:** add `rose` (rosa_canina) to `generate_agrigen.py` CROPS → flowerless shrub skeleton. Caveat-class.
- **procedural:demeter:** `generate_demeter.py --species rose` → flowerless stems+leaves. Caveat-class (Academic-NC, internal).
- **L-Py rose:** deferred (needs an authored rose .lpy).

## Architecture / reuse

Most work = adding rose entries to existing crop-parametric CROPS dicts + new registry entries + the ROSE-X ingest + Infinigen
wiring + the rose subject/spotlight/seed/reference-photo. New code is concentrated in: registry entries (sourcing.py),
seed.py (rose subject), spotlight.py (rose entry), a possible Infinigen generator script, and possible ROSE-X format handling.

## Testing

Per-tier unit tests mirroring the maize pattern (shared-DB-safe: get-or-create subject Task, scope ModelOutput asserts by
(source, task_id), never bare `.one()` on Task.title): rose CROPS/registry entries; rose-subject seed idempotent + buildable
spotlight; ingest routing per class. Real ingests verified by DB query. Full suite stays green.

## Honesty / caveats

ROSE-X is _Rosa rugosa_ (a specific species); found roses span multiflora/rugosa/chinensis (label species). Procedural roses are
flowerless (AgriGen/Demeter) or generic-bloom (Infinigen) — labeled caveat-class. Objaverse has no rose (documented gap).

## Out of scope

- The ~/flower bespoke procedural rose (separate repo, plateaued) — would feed the procedural slot later if it passes its critic gate.
- L-Py authored rose; rose recon Mode-B scoring; Infinigen rose-preset tuning beyond a recognizable bloom.

## Acceptance criteria

1. A `rose` spotlight subject exists with the CC0 reference photo; `build_spotlight("rose")` works.
2. Tier 1: ≥1 ROSE-X scan (point cloud) and ≥1 ROSE-X volumetric output hosted on the rose subject (CC0 provenance), OR the
   acquisition guard fired and was reported.
3. Tier 2: curated found:sketchfab roses + recon outputs + a partcrafter rose hosted.
4. Tier 3: procedural:infinigen bloom + agrigen/demeter rose caveats hosted.
5. Full test suite green; per-tier code reviewed; final whole-branch review merge-ready.
