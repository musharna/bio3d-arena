# Arabidopsis + Pine Coverage Parity — Result

> Date: 2026-06-26
> Plan: docs/superpowers/plans/2026-06-26-arabidopsis-pine-coverage-parity.md
> Spec: docs/superpowers/specs/2026-06-26-arabidopsis-pine-coverage-parity-design.md

## Outcome

Both gap subjects raised from **1 source each** to multi-source parity with the covered four,
using only sourceable (non-imaging) inputs.

| Subject              | Task id | Before                    | After (outputs / sources)   |
| -------------------- | ------- | ------------------------- | --------------------------- |
| Arabidopsis thaliana | 10      | 15 outputs / **1** source | **28 outputs / 12 sources** |
| Pinus sylvestris     | 13      | 15 outputs / **1** source | **28 outputs / 11 sources** |

For context, the covered four: tomato 17 sources, maize 15, rose 14, soybean 12. Arabidopsis
(12) now equals soybean; Pine (11) sits just below — both firmly in the parity class and fully
rankable in the pairwise arena.

## Per-source breakdown

**Arabidopsis (task 10) — 12 sources:**

| Source                                                        | n   | Tier                 |
| ------------------------------------------------------------- | --- | -------------------- |
| bio3d-arena (pre-existing procedural)                         | 15  | pre-existing         |
| api:fal:hunyuan3d-v2 / v3 / trellis / triposr / hyper3d       | 5×1 | deterministic        |
| api:replicate:hunyuan3d-3.1 / trellis                         | 2×1 | deterministic        |
| frontier:partcrafter                                          | 1   | deterministic        |
| procedural:lpy (authored rosette+bolt)                        | 1   | deterministic        |
| romi-arabidopsis (ROMI real scan, Zenodo 10379172, CC-BY-4.0) | 1   | best-effort (filled) |
| found:sketchfab (3 real CC-BY models)                         | 3   | best-effort (filled) |

**Pine (task 13) — 11 sources:**

| Source                                                  | n   | Tier                 |
| ------------------------------------------------------- | --- | -------------------- |
| bio3d-arena (pre-existing procedural)                   | 15  | pre-existing         |
| api:fal:hunyuan3d-v2 / v3 / trellis / triposr / hyper3d | 5×1 | deterministic        |
| api:replicate:hunyuan3d-3.1 / trellis                   | 2×1 | deterministic        |
| frontier:partcrafter                                    | 1   | deterministic        |
| procedural:lpy (authored conifer)                       | 1   | deterministic        |
| found:sketchfab (4 real CC-BY models)                   | 4   | best-effort (filled) |

## Best-effort source ledger (attempt-or-skip-and-log)

| Source             | Arabidopsis                                                     | Pine                                                         |
| ------------------ | --------------------------------------------------------------- | ------------------------------------------------------------ |
| ROMI / scan        | **FILLED** (ROMI real scan)                                     | SKIPPED — no public CC conifer scan within budget (multi-GB) |
| found:sketchfab    | **FILLED** ×3                                                   | **FILLED** ×4                                                |
| objaverse          | SKIPPED — no usable LVIS category (mislabel risk)               | SKIPPED — same                                               |
| procedural:agrigen | SKIPPED — no Arabidopsis descriptor in agrigen repo             | SKIPPED — no Pine descriptor                                 |
| procedural:demeter | SKIPPED — species unsupported (maize/soybean/tobacco/rose only) | SKIPPED — unsupported                                        |

Honesty contract upheld throughout: skips were preferred over dishonest fills; all 7 Sketchfab
assets were verified against the live Sketchfab API (real uid/author/CC-BY license); genus-level
pine assets are labeled truthfully by their actual names (same precedent as maize/rose found
assets), never falsely claiming "Pinus sylvestris".

## Verification

- Deterministic floor met: each subject has 7 API recon + L-Py + PartCrafter = 9 deterministic
  generators (plus pre-existing procedural + best-effort).
- Full test suite: **268 passed**, 1 pre-existing warning.
- Viewer real-execution check: app boots; `/`, `/api/next`, `/benchmark`, `/leaderboard`,
  `/spotlight/arabidopsis` all HTTP 200; a new PartCrafter GLB is served at
  `/assets/uploads/<hash>.glb` → HTTP 200. (Pine has no spotlight page — only 5 curated
  subjects do — which is unrelated to arena coverage.)
- Morphology critic gate (controller visual): authored L-Py Arabidopsis = flat rosette + thin
  bolt with siliques; L-Py Pine = central trunk + whorled branches (excurrent conifer); ROMI
  scan = recognizable real Arabidopsis raceme. All pass.

## Known follow-ups (non-blocking)

- L-Py, ROMI scan, and Sketchfab rows were ingested `--no-score` (recon scorer not run live);
  backfillable via `POST /admin/rescore` / `rescore_all`. Does not affect pairwise arena.
- License-string format: `romi-arabidopsis` uses `CC-BY-4.0` (hyphen) vs scan-dataset siblings'
  `CC-BY 4.0` (space) — harmonize in a normalization pass.
- Barley (task 18, volumetric root) remains deferred — different modality, out of this scope.
