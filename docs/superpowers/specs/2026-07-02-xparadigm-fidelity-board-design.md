# Cross-Paradigm Biological-Fidelity Board — Design Spec

> Status: design (brainstorming output). Not yet planned/implemented. Author session: AmberFinch (a9baa6b4), 2026-07-02.

## Goal

A new arena view that ranks outputs **across paradigms** (image_recon / procedural_llm / procedural_expert / retrieval / text_native / agentic) on **absolute biological ground-truth fidelity**, per taxon — the objective complement to the existing within-paradigm _preference_ arena (which is correctly within-paradigm-only because preference is not commensurable across paradigms). Answers the headline question the preference arena structurally cannot: _"which paradigm/model best reconstructs each taxon?"_

## Background / grounding

- Direction triage → **OWNED-AT-SEAM** (see `memory/xparadigm_fidelity_triage_2026-07-02.md`): nearest owner 3DGen-Bench is preference/proxy, general objects, generative-only; the seam = cross-paradigm + biological-GT-fidelity + per-taxon. Moat = our held-out GT corpus + trait/organ metrics + multi-paradigm outputs in one DB.
- **Commensurability:** an absolute measure vs the _same_ per-taxon GT is comparable across paradigms (unlike preference). Two constraints fall out:
  1. SP4 "geometry is not enough" (Chamfer a weak proxy) → the board must **not** be a single geometric ELO. Multi-axis, each labeled by validation status.
  2. `capture_scan` outputs _are_ the GT reference scans → shown as a reference upper-bound row, never a ranked competitor.
- **Axis sync with RusticDune** (owns D-Complete): completeness is the one κ-validated cross-paradigm axis (binary κ=0.64; the 4-way is experimental 0.42 → anchor on **binary/`score` fraction**, not 4-way). They endorse the multi-axis scorecard and confirmed read-only `/api/completeness.json` is the right seam; no overlap with their D-Gen firming work.

## Architecture

A **read-only aggregation** layer + a board page. It **reads** three existing per-output signals, joins on `output_id`, maps `output → generator → paradigm` and `output → taxon`, aggregates per `(taxon, paradigm)`, and renders a per-taxon scorecard. It **computes/writes nothing** to the fidelity source tables.

### The three axes (each column carries a validation badge — no single blended score)

| Axis                       | Source                                          | Field                                                            | Validation badge                 | Direction              |
| -------------------------- | ----------------------------------------------- | ---------------------------------------------------------------- | -------------------------------- | ---------------------- |
| **Completeness** (PRIMARY) | `/api/completeness.json` / `completeness` table | `score` (required-organ fraction) + `category=='complete'` share | **validated — binary κ=0.64**    | higher = better        |
| **Geometry** (secondary)   | `metric`                                        | `fscore` (and `chamfer`)                                         | **geometric proxy — weak (SP4)** | fscore higher = better |
| **Trait** (experimental)   | `trait_score`                                   | `botanical_accuracy`                                             | **experimental — κ-negative**    | higher = better        |

Ranking within a taxon is by the **primary** axis (completeness: `%complete`, tie-break mean `score`), with geometry/trait shown as informational columns. The header states explicitly: _no single blended fidelity score; completeness is the validated ranker, the others are context._

### Structure (per taxon block)

For each of the 6 inventory taxa (Arabidopsis, tomato, maize, pine, rose, soybean):

- One row per **paradigm** that has ≥1 scored output for that taxon.
- Columns: `paradigm`, `n` (outputs), **completeness** (`%complete`, mean `score`), **geometry** (mean `fscore`, `n_geom`), **trait** (mean `botanical_accuracy`, `n_trait`), and **best model** (the generator in that paradigm with the highest completeness `score`, by display name) — this answers the "which _model_" half.
- `n_geom`/`n_trait` are shown so sparse axes are visible (geometry/trait cover only some paradigms).
- `capture_scan` appears as a visually-separated **reference row** (GT upper-bound), not ranked.

## Data flow

1. `fidelity_scorecard(db)` reads: `completeness` rows (via existing `service.completeness_rows`), `metric` rows, `trait_score` rows.
2. Index each by `output_id`; resolve `generator_id → paradigm` and `output → taxon` (task's `TraitRubric.taxon`, matching the completeness API).
3. Aggregate per `(taxon, paradigm)`: `n`, mean completeness `score`, `%complete`, mean `fscore` + `n_geom`, mean `botanical_accuracy` + `n_trait`, best-model.
4. Rank paradigms within each taxon by the primary axis.
5. Return a structured dict `{taxa: [{taxon, rows:[...], reference:[capture_scan row]}], axes_meta:[{key,label,badge,direction}]}`.

## Components / files

- **`app/fidelity.py`** (new): `fidelity_scorecard(db) -> dict` (pure aggregation, unit-testable with a seeded DB).
- **`app/main.py`** (modify, additive only): `GET /api/fidelity.json` (returns the dict) + `GET /fidelity` (renders the page). Freeze is lifted (PR #7 merged @f338554; RusticDune reservations released); additive routes only.
- **`templates/fidelity.html`** (new): per-taxon scorecard tables, axis badges, reference row, "no single score" explainer.
- **nav link** in the base template (one line).
- **`tests/test_fidelity.py`** (new).

## Prerequisite (in progress)

Completeness must be scored across **all** paradigms for the 6 taxa (the real study DB had no `completeness` table until now — validation ran on a copy). Schema migrated (`init_db`, additive) + `scripts/score_completeness.py` running over 228 eligible outputs (study DB, snapshotted). Board shows "no data yet" gracefully until rows exist.

## Edge cases

- Taxon with no inventory → not shown (only the 6 rubric taxa).
- Paradigm with 0 scored outputs for a taxon → omitted from that block.
- Output missing an axis → excluded from that axis's mean; `n_axis` reflects true coverage.
- No completeness data yet → API returns `taxa: []` / page shows an empty-state message.
- `capture_scan` → reference row only.
- Ties in the primary axis → tie-break by mean `score`, then `fscore`.

## Non-collision

Reads completeness read-only (`service.completeness_rows` / table); no edits to `app/completeness.py`, `app/dgen.py`, or `scripts/run_dgen.py`. Only shared file touched is `app/main.py` (additive routes) + base template (nav). When RusticDune later promotes D-Gen outputs to the real DB + scores their completeness, they slot into this board automatically (same tables).

## Success criteria

1. `GET /api/fidelity.json` returns per-taxon × paradigm aggregates with all three axes, per-axis coverage counts, validation metadata, and best-model.
2. `GET /fidelity` renders the scorecard: per-taxon blocks, paradigms ranked by completeness, geometry/trait as labeled context columns, `capture_scan` reference row, explicit "no single score / validation status" framing.
3. Aggregation is correct on a synthetic seeded DB (means, %complete, best-model, ranking, missing-axis handling).
4. Empty-state handled (no completeness rows → graceful).
5. Full test suite green; no writes to fidelity source tables.

## Out of scope (v1)

- Per-generator (not per-paradigm) full ranking — only "best model" per cell in v1.
- Backfilling geometry/trait for paradigms that lack them (shown as sparse; a later pass could densify).
- Cross-taxon aggregate ranking (taxa differ in difficulty; per-taxon only).
- Any change to the preference arena or the within-paradigm boards.
