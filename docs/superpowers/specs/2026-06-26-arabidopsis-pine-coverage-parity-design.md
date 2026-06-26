# Arabidopsis + Pine Coverage Parity — Design Doc

> Date: 2026-06-26
> Status: Approved (scope + depth + barley + ROMI + skip-and-log all confirmed by user)

## 1. Purpose

"Build the benchmark fully out" (user directive, no new imaging). Scope chosen:
**fill coverage gaps** at **full parity**. Two benchmark subjects are structurally
under-covered and therefore unrankable in a pairwise generator arena:

| Subject              | Task id | Outputs | Distinct sources (live DB, worktree arena.db) |
| -------------------- | ------- | ------- | --------------------------------------------- |
| Arabidopsis thaliana | 10      | 15      | **1** — all `bio3d-arena` procedural          |
| Pinus sylvestris     | 13      | 15      | **1** — all `bio3d-arena` procedural          |

Compare to the covered four (live DB): tomato 77 outputs / 17 sources, maize 46 / 15,
rose 27 / 14, soybean 20 / 12. A pairwise arena needs ≥2 _distinct generators_ per task to
rank anything; Arabidopsis and Pine currently have one source each, so the leaderboard
cannot compare generators on them at all.

**Goal:** raise Arabidopsis (10) and Pine (13) to the same multi-source spread as the
covered four, using only sourceable (non-imaging) inputs.

## 2. Non-Goals (explicitly out of scope)

- **No new imaging.** Reference photos and assets are sourced from existing public/CC
  material only. Downloading an existing public CC photo is sourcing, not imaging.
- **Barley (id 18) is deferred.** It is a volumetric root-MRI task, not a single-photo
  subject; the image→3D pipeline does not apply. Thickening it means rare public
  root-scan datasets — a separate track, not this build.
- **#25 (difficulty tiers + ground truth) and #21 (multi-view)** remain deferred (need
  imaging / GT capture).
- No new generators, no ranking-algorithm changes, no UI changes. This is a
  content/coverage build on the existing, proven pipeline.

## 3. Architecture — reuse the existing per-crop pipeline

Every generator/source script already keys off a per-crop `CROPS` (or equivalent) dict and
ingests via `app.ingest`, attaching outputs to a subject by task title, committing per
object. The covered four were built exactly this way. This build adds two crop keys
(`arabidopsis`, `pinus`) to each relevant script plus their sourced inputs. No new
abstractions.

Task titles (live DB, must match exactly):

- Arabidopsis: `"Arabidopsis thaliana — single-image → 3D reconstruction"` (id 10)
- Pine: `"Pinus sylvestris — single-image → 3D reconstruction"` (id 13)

Reference-photo convention (matches existing four):
`data/assets/reference/{arabidopsis,pinus}_ref.jpg` + sibling `_ref.json` provenance
(source URL, license, attribution).

## 4. Source matrix (parity target)

Per subject, attempt every source class. Reliability tiers govern expectations and the
honesty contract (Section 6).

| Source class               | Generator slug(s)                                                                                                                                               | Script                    | Tier                          | Notes                                                                                                                                                                                                                                                                                                  |
| -------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------- | ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **API recon**              | `api:replicate:trellis`, `api:replicate:hunyuan3d-3.1`, `api:fal:triposr`, `api:fal:trellis`, `api:fal:hyper3d`, `api:fal:hunyuan3d-v3`, `api:fal:hunyuan3d-v2` | `generate_api_recon.py`   | **Deterministic**             | Core of the leaderboard. Needs CC ref photo + keys + budget. Add both crops to `CROPS`.                                                                                                                                                                                                                |
| **Procedural — L-Py**      | `procedural:lpy`                                                                                                                                                | `generate_lpy.py`         | **Deterministic (authoring)** | Author Arabidopsis (rosette + bolting inflorescence) and Pine (conifer whorl) L-systems. Currently tomato/maize/soybean.                                                                                                                                                                               |
| **Frontier — PartCrafter** | `frontier:partcrafter`                                                                                                                                          | `generate_partcrafter.py` | **Deterministic**             | Image→3D from the CC ref photo — species-agnostic, works for any subject. Add both crops to `CROPS`.                                                                                                                                                                                                   |
| **Procedural — agrigen**   | `procedural:agrigen`                                                                                                                                            | `generate_agrigen.py`     | **Best-effort**               | CORRECTED: agrigen's UnifiedGenerator only has tomato/maize/rose plant descriptors; Arabidopsis/Pine descriptors don't exist in the agrigen repo. Attempt only if a descriptor exists; else skip-and-log. (agrigen's ROMI _Arabidopsis_ asset is a real scan, handled in the Real-scan row, not here.) |
| **Procedural — Demeter**   | `procedural:demeter`                                                                                                                                            | `generate_demeter.py`     | **Best-effort**               | CORRECTED: Demeter is cereal/crop-specific; Pine and likely Arabidopsis are unsupported. Attempt only if the species is modeled; else skip-and-log.                                                                                                                                                    |
| **Real scan**              | `scan:*`                                                                                                                                                        | `source_scans.py`         | **Best-effort**               | **Arabidopsis: pull agrigen's ROMI space-carved point cloud** (real captured scan already on disk — confirmed yes). Pine: research a public conifer scan; skip-and-log if none.                                                                                                                        |
| **Found — Sketchfab**      | `found:sketchfab`                                                                                                                                               | `generate_sketchfab.py`   | **Best-effort**               | Pine: abundant CC models. Arabidopsis: rare — may yield 0–1. Skip-and-log if none.                                                                                                                                                                                                                     |
| **Found — Objaverse**      | `objaverse`                                                                                                                                                     | `source_objaverse.py`     | **Best-effort**               | Same skew as Sketchfab. Currently maize/tomato.                                                                                                                                                                                                                                                        |

Minimum success bar (guaranteed-deterministic): each subject ends with the 7 API recon
outputs + L-Py (authored) + PartCrafter (image-based) = 9 distinct generators → fully
rankable. agrigen, Demeter, scans, Sketchfab, Objaverse are best-effort upside (sourced or
skip-and-logged), not gates.

## 5. Inputs sourcing (no imaging)

- **Reference photos:** one CC/public-domain front-on whole-plant photo per subject
  (Wikimedia Commons or iNaturalist CC-BY preferred). Arabidopsis: a potted rosette with
  bolting stem on a clean background, mirroring how the covered four use a clean isolated
  subject. Pine: a young _Pinus sylvestris_ (whole tree or seedling) on a clean background.
  Save image + `_ref.json` provenance. Relicense note: like the other private refs, flag
  for relicense before any public release if a non-redistributable photo is used.
- **ROMI Arabidopsis scan:** copy/convert from agrigen on-disk assets (see
  `hunyuan_reconciliation_2026-06-26` memory for ROMI location refs); attribute ROMI /
  Zenodo 10379172, record its real license.

## 6. Honesty contract (hard rule)

- **Deterministic sources must land** (given keys + budget). If one fails, that is a bug to
  fix, not a silent skip.
- **Best-effort sources are sourced-or-skipped with a logged reason** — never substitute a
  generic, wrong-species, or mislabeled asset. This is the rule the "rogue tomato" /
  stray-label incidents established: an output's `source`, `title`, species, and input image
  must all be truthful and consistent.
- **Final deliverable: a per-subject × per-source coverage table** showing filled vs.
  unavailable (with reason), so the gap-fill result is auditable at a glance.

## 7. Budget & feasibility

- API: 7 models × 2 subjects ≈ 14 calls. Bake-off spent ~$7.5 of $20 → fits comfortably.
- API keys (FAL_KEY, REPLICATE_API_TOKEN) from `~/.zshrc`; never logged. Run via
  `.venv/bin/python` after `source ~/.zshrc`.
- The pipeline, ingest path, and recon transports are already debugged (6 live-API bugs
  fixed in the bake-off). This build adds data, not new failure surface.

## 8. Testing

- **Per-script unit:** each modified generator script's `CROPS` addition exercised with a
  synthetic/injected provider (mirroring existing tests) so the new crop entries wire to the
  correct task title and `source`/`license`/`attribution` fields.
- **Real-execution check (per the user's standing rule):** after a run, list the real DB
  rows for tasks 10 and 13 grouped by `source`, and confirm each new asset file exists on
  disk and loads in the viewer. Synthetic-fixture test + one real-execution check per source
  class touched.
- Existing suite (68 tests) must stay green.

## 9. Deliverable

Arabidopsis (10) and Pine (13) each raised from 1 source to multi-source parity:
9 guaranteed generators (7 API recon + L-Py + PartCrafter), plus any best-effort
agrigen/Demeter/found/scan/objaverse that sourced cleanly (incl. ROMI real scan for
Arabidopsis) — with a coverage table reporting exactly what filled and what was skipped
and why. Committed per source. Merged to master like the prior coverage tracks.
