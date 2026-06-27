# Multi-View Recon Track (#21) — Design

> Status: approved (2026-06-27). Scope: a single-image→NVS→multi-view-recon track, initially pine + arabidopsis.

## Goal

Add a **multi-view reconstruction track** to the recon benchmark: from each subject's single
reference photo, synthesize N consistent novel views (NVS), feed them to the **existing**
multi-view→mesh recon providers, and ingest the results as `source="recon:*"`. Motivated by the
finding that single-image recon fundamentally fails on thin/needle structures (pine); multi-view
is the research-backed robust path.

## Context (already built — reuse, don't rebuild)

- `app/image3d.py`: `mode="multiview"` + `MULTIVIEW_PROVIDERS` (`fal-ai/trellis/multi`,
  `fal-ai/hunyuan3d/v2/multi-view`) that take N view images → mesh GLB.
- `scripts/generate_api_multiview.py`: tested core `generate_api_multiview(db, views, providers, env,
score_fn, task_title)` — hosts each provider's mesh as `source="recon:*"` bound to the subject Task.
  Currently `main()` reads views from `VIEWS_DIR` and is hardcoded to the tomato Task.
- `app/sourcing.py`: `source.startswith("recon:")` already classified as AI recon.
- The **gap**: nothing GENERATES the views. `main()` expects them to already exist on disk.

## Non-goals (out of scope)

- Real multi-view capture / photogrammetry / COLMAP SfM (AI-generated views can't be posed by SfM —
  we feed feed-forward MV models directly). 3D Gaussian Splatting / NeRF / model training.
- New DB tables (reuse the `recon:` source convention + the existing recon `Task`/`ReconTask`).
- Fixing single-image recon; this is a separate, additive track.

## Global constraints

- API keys (`REPLICATE_API_TOKEN`, `FAL_KEY`) come from env, **never logged/pasted**.
- Reuse `ingest.register_output` (content-hash dedup) and the `recon:*` source class. Human
  voting/ranking path untouched. No new schema (create_all-only convention preserved).
- Honest N/A / skip-and-log: a subject with no NVS views, or a provider error, is skipped + logged,
  never faked.

## Architecture

```
reference photo (1)
   → NVS provider (Zero123++ class): 1 image → 6 fixed-pose views
   → cache views to VIEWS_DIR/<subject>/
   → generate_api_multiview(views, MULTIVIEW_PROVIDERS)  [EXISTING]
   → recon:* mesh outputs bound to the subject's recon Task
   → (later) recon_service scoring when AgriGen is up
```

## Components

### 1. NVS provider (`app/image3d.py`)

A new function `generate_nvs(image_bytes, *, api_key, model, transport=None, ...) -> list[bytes]`
returning the synthesized view images, mirroring the existing submit→poll→download provider pattern
with an injectable transport for tests. Plus an `NVS_PROVIDERS` registry entry.

- **Concrete candidate (pin + verify in the plan):** Replicate `jd7h/zero123plusplus` — 1 square
  image (≥320px) → 6 views at fixed poses (azimuth 30/90/150/210/270/330, elevation
  30/−20/30/−20/30/−20). Returns either 6 separate images or a tiled sheet; the provider wrapper
  normalizes to `list[bytes]` of 6 views (de-tile if needed).
- **Pose-compatibility constraint (resolve in the plan via a live call):** the MV recon provider
  must accept these views. Zero123++'s 6-view output is the canonical InstantMesh input; the plan
  verifies the chosen `MULTIVIEW_PROVIDERS` entry reconstructs acceptably from these 6 views (and, if
  a provider needs different poses/count, either adjust N or select a compatible MV provider). If no
  API MV provider accepts the NVS views, that's a plan-stage blocker to surface, not silently ship.

### 2. Orchestration (`scripts/generate_multiview_recon.py`)

Per subject: read the reference photo → `generate_nvs` → write the 6 views to `VIEWS_DIR/<subject>/`
→ call the existing `generate_api_multiview` with the subject's recon `task_title`. A `SUBJECTS` map
(slug → reference path + task title) parameterizes it (the existing tomato hardcoding generalized).
Idempotent: skip NVS if cached views already exist (unless `--refresh`); recon dedups by content hash.
`--subject` selects one; default runs the configured scope.

### 3. Scope (initial)

`pinus` + `arabidopsis` (the two hard subjects that motivated this), using their current reference
photos (`pinus_ref.jpg`, `arabidopsis_ref.jpg`). `SUBJECTS` is extensible to all 4 ReconTask
subjects. N = 6 views (Zero123++ default).

### 4. Eval / surfacing

Outputs are `source="recon:*"` → already shown as AI recon in the spotlight/arena. Scoring via
`recon_service.score_and_store` when AgriGen `/score` is up (deferred — currently down); the track
ships producing meshes regardless.

## Data flow

ref photo → NVS (6 views) → `VIEWS_DIR/<subject>/*.png` → `generate_api_multiview` → `recon:<provider>`
`ModelOutput`s under the subject's recon Task (GLB in the asset store, deduped).

## Error handling / edge cases

- NVS returns < 2 usable views → skip that subject, log (the MV step needs ≥2).
- NVS API error → skip subject, log; other subjects continue.
- MV provider error → best-effort per provider (existing `generate_api_multiview` behavior).
- Missing reference photo → skip + log.
- Re-run with cached views → reuse unless `--refresh`; recon dedups identical meshes.

## Testing (TDD, per unit)

1. `generate_nvs`: fake transport → returns the expected `list[bytes]` of 6 views; de-tile logic if
   the model returns a sheet; error → raises a typed error.
2. Orchestration core (subject → NVS → cache → MV recon): injected fake NVS + fake MV `generate_*`,
   asserts views cached per subject and `recon:*` outputs registered under the right Task; <2 views →
   skip; NVS error → skip-and-continue.
3. Ingest: outputs carry `source="recon:*"` and bind to the subject's recon Task.
4. Regression: human path + existing single-image recon untouched; full suite green.

Real-execution check (plan, key-gated): one live NVS call on the arabidopsis photo → 6 views →
one MV-recon call → render + eyeball the mesh, confirming the NVS↔recon pairing works end-to-end
before wiring the full scope.
