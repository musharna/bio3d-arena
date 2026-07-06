<!-- ROOT_CAUSE_OK: design spec, not a bug fix -->

# Formal Difficulty Roster (v1) — Design Spec

**Date:** 2026-07-04
**Status:** approved (brainstorming), pre-plan
**Goal:** Turn the thin, ad-hoc, recon-only difficulty scaffold into a **formal, multi-axis, per-taxon geometric-difficulty roster** grounded in published hardness axes, applied across **all** paradigms, and surfaced as a **paradigm × tier grid** — the seam no competitor occupies.

## Context

A grounded direction triage (persisted in `memory/difficulty_roster_triage_2026-07-04.md`) returned **OWNED-AT-SEAM**:

- **Dora-Bench** (arXiv 2412.17808, CVPR 2025) owns the generic move — tier 3D subjects by a principled geometric-complexity metric — but it is **single-axis** (salient-edge-density) over **manufactured objects, zero organisms**. Cite it; do not claim concept novelty.
- **FloraForge** (arXiv 2512.11925) owns bio-procedural generation but builds **no difficulty structure**; plant-recon benchmarks (GaussianPlant 2512.14087) leave hardness as prose, never operationalized.
- **Open seam, 0 occupants:** {biological organisms} × {multi-axis hardness taxonomy} × {cross-paradigm generation performance}. bio3d's existing "recon degrades easy→hard, procedural improves" gradient is the differentiator.

**Live current state (verified this session):**

- `app/difficulty.py`: `TaskDifficulty` side table (`task_id → tier ∈ {easy, moderate, hard}` + free-text `rationale`); `set_task_difficulty` upserts by task_id; `tier_scorecard` aggregates the **existing objective metrics** (`Metric.chamfer/fscore/species_verdict`, `OrganMetric.botanical_fidelity`) by (tier × generator) and **never touches the human Bradley-Terry path**.
- `scripts/assign_difficulty.py`: hand-maps **4 taxa only** (tomato=easy, maize=moderate, arabidopsis=hard, pinus=hard), keyed by `ReconTask.species_slug` — so procedural/text/agentic/scan tasks and 3 further taxa are untiered.
- A **single arena task per taxon aggregates outputs from every paradigm** (e.g. task 19 "Rosa — single-image → 3D reconstruction" holds recon, procedural, text-native, agentic, and scan outputs together). Difficulty is therefore a property of the **taxon**, not the task.
- `Generator.paradigm` (live values): `retrieval`, `image_recon`, `procedural_llm`, `capture_scan`, `text_native`, `procedural_expert`, `agentic` (+ 2 empty). This column is the paradigm axis for the grid.
- **Schema convention (load-bearing):** the schema is **create_all-only**; per-task auxiliary data lives in **side tables** (`ReconTask`, `OrganMetric`, `TaskDifficulty`), explicitly _"so `Task` stays generic and we avoid an ALTER on the create_all-only schema"_ (`app/models.py:411,504,519`). `app/database.py::_ensure_columns` self-heals _additive_ columns on boot (e.g. `generator.paradigm`, `model_output.hidden_at`). **A brand-new table is picked up by `create_all` with no ALTER needed.**

### Correction vs the verbally-approved design

The approved design said "add a nullable `species_slug` to `Task`." That contradicts the side-table convention above. This spec instead adds a **new `TaxonDifficulty` side table** (picked up by `create_all`, no `Task` ALTER) and resolves task→taxon from the title binomial. Behavior and intent are unchanged; only the storage mechanism is corrected to match the project pattern.

## Approved decisions

- **Multi-axis rubric, 5 axes, each scored 0–2**, summed → tier. Axes + provenance:
  | Axis | Grounding |
  |---|---|
  | `fine_detail` (fine repeated detail) | Dora-Bench salient-edge-density (2412.17808) |
  | `self_occlusion` | Yunus et al. CGF-2024 (2403.15064) |
  | `non_rigidity` (deformation) | Yunus et al. CGF-2024 |
  | `topology` (genus / disconnected parts) | ours (unclaimed for organisms) |
  | `thin_structure` (thin-structure fraction) | ours |
- **Tier thresholds** (sum 0–10): `0–3 → easy`, `4–6 → moderate`, `7–10 → hard`. Thresholds are named constants, tunable in one place.
- **Scoring source (v1): hand-scored-principled.** A 0–2 score + one-line rationale per (taxon, axis), authored against the cited axis definitions. Computed corroboration of the 3 computable axes (topology via trimesh genus, thin-structure via thickness, fine-detail via edge-density) from GT meshes is a **deferred follow-on** — not feasible uniformly (not every taxon has a GT scan) and measuring a taxon's difficulty from one specimen mesh conflates specimen with taxon.
- **Per-taxon difficulty applied to all of a taxon's tasks** (a taxon may have both a recon task and a botanical-plausibility task — both inherit the one tier).
- **Headline deliverable = paradigm × tier grid over the existing objective metrics** — deployable now, needs no human vote volume.

## Global constraints

- **Read-only on the real study DB.** All scoring/materialize runs on a COPY. NEVER `BIO3D_DATABASE_URL=study`. Test runner `.venv/bin/pytest`.
- **Never touch the human Bradley-Terry / Elo path.** The scorecard aggregates only pre-computed objective metrics, never recomputes BT, exactly as `tier_scorecard` does today.
- **Fail-loud, no silent fallback.** A taxon with a missing or partial rubric score (fewer than all 5 axes, or any axis outside 0–2) raises — it is never defaulted to a tier or silently dropped. A task whose title does not parse to a non-empty species slug raises. Coverage gaps (a task whose resolved species has no `TaxonDifficulty` row) are surfaced fail-loud at the **operational boundary**: `materialize_task_difficulty` _reports_ them in a `skipped` list (so it is testable/composable over a shared global `Task` table), and the seeding **script refuses to proceed (raises)** if `skipped` is non-empty. A gap is never silently left untiered.
- **Honor the create_all-only + side-table schema convention.** No `ALTER TABLE task`. New data → new side table, picked up by `create_all`.
- **Tier vocabulary stays exactly `{easy, moderate, hard}`** — do not widen.
- **`tier_scorecard`'s existing (tier × generator) output stays behavior-identical** — the paradigm grid is an addition, not a replacement.

## Architecture

Five units, each independently testable.

### 1. Rubric (`app/difficulty_rubric.py`, new)

- `AXES: tuple[str, ...]` — the 5 axis keys above.
- `TIER_THRESHOLDS` — the (easy/moderate/hard) cut points as named constants.
- `tier_for_scores(scores: dict[str, int]) -> str` — validates all 5 axes present and each ∈ {0,1,2} (else `raise ValueError`), sums, maps to a tier via thresholds. Pure function.
- `RUBRIC: dict[str, dict]` — per-taxon authored data: `species_slug → {"scores": {axis: 0..2, ...}, "rationale": {axis: str, ...}}` for all 7 in-scope taxa (`arabidopsis_thaliana`, `pinus_sylvestris`, `rosa`, `solanum_lycopersicum`, `zea_mays`, `glycine_max`, `hordeum_vulgare`). Each taxon's rationale cites the axis basis.
- `taxon_tier(species_slug) -> str` and `taxon_axes(species_slug) -> dict` — look up `RUBRIC`, fail-loud on unknown/partial.

### 2. `TaxonDifficulty` side table (`app/models.py`)

New table, keyed by `species_slug` (unique). Columns: `species_slug`, `tier`, `axis_scores` (JSON text: `{axis: int}`), `rationale` (JSON text: `{axis: str}`), `updated`. Mirrors the `TaskDifficulty`/`ReconTask` side-table pattern — picked up by `create_all`, no ALTER. This is the per-taxon **source of truth**; `TaskDifficulty` becomes the materialized per-task projection of it.

### 3. Task→taxon resolver + materialize (`app/difficulty.py`)

- `species_slug_for_task(task) -> str` — normalize the title's binomial prefix: `title.split("—")[0].strip().lower().replace(" ", "_")` (e.g. `"Rosa — single-image → 3D reconstruction"` → `"rosa"`, `"Zea mays — botanical plausibility"` → `"zea_mays"`). Genus-only titles (`"Rosa"`) yield a one-word slug. Raise if the result is empty.
- **Consistency invariant (test):** for every task that _has_ a `ReconTask`, `species_slug_for_task(task) == ReconTask.species_slug`. Guards the parser against title drift.
- `materialize_task_difficulty(db, commit=True) -> {"materialized": int, "skipped": list, "taxa": int}` — for every `Task`, resolve species → look up `TaxonDifficulty` → upsert a `TaskDifficulty(task_id, tier, rationale=<taxon rationale summary>)`. Idempotent. A task whose resolved species has no `TaxonDifficulty` row is **collected into `skipped`, not raised** — so the function is testable/composable over a shared global `Task` table (mirrors the existing `assign_all` `{assigned, skipped}` contract). The fail-loud policy lives in the script (unit 5): it raises if `skipped` is non-empty. `commit=False` lets tests run under transaction rollback.
- `set_task_difficulty` is retained unchanged (still used for any manual override / existing callers).

### 4. Paradigm × tier scorecard (`app/difficulty.py`)

- `paradigm_tier_scorecard(db) -> list[dict]` — same aggregation shape as `tier_scorecard` but grouped by (`tier` × `Generator.paradigm`) instead of (tier × generator). Reuses the identical objective-metric plumbing (`Metric`, `OrganMetric`); means skip `None`, never zero-fill; canonical tier order + `untiered` bucket; empty-paradigm generators bucket under `"unspecified"`. `tier_scorecard` itself is untouched.

### 5. Seeding driver + view (`scripts/assign_difficulty.py` rewrite, `app/main.py` route)

- `scripts/assign_difficulty.py` — seed `TaxonDifficulty` from `RUBRIC` (upsert per taxon, via `tier_for_scores`), then call `materialize_task_difficulty`. **Raises if the returned `skipped` is non-empty** (fail-loud on any uncovered task — the operational boundary). Prints a disposition summary (taxa seeded, tasks materialized). Uses the existing `config.is_safe_test_db_target` default-deny guard before writing (never the real study DB).
- `/difficulty` view — extend the existing page/route to render the paradigm × tier grid (heatmap of the objective metrics per paradigm per tier) alongside the existing generator scorecard. The exact template/route wiring is nailed in the plan.

## Data flow

`RUBRIC` (hand-scored, cited) → `assign_difficulty.py` seeds `TaxonDifficulty` (per-taxon tier + axis scores) → `materialize_task_difficulty` projects onto `TaskDifficulty` (per-task, via title→species resolver) → `paradigm_tier_scorecard` aggregates existing objective metrics by paradigm × tier → `/difficulty` renders the grid. All on a DB copy; human BT path never touched.

## Error handling

Every gate fail-loud: partial/invalid axis scores raise in `tier_for_scores`; unknown/partial taxon raises in `taxon_tier`; unparseable title raises in `species_slug_for_task`. A task resolving to a species with no `TaxonDifficulty` is reported in `materialize_task_difficulty`'s `skipped` list and raised by the **script** (`assign_difficulty.main`) — fail-loud at the operational boundary, never a silent untiered drop. No silent tier defaults.

## Testing

1. `tier_for_scores` — table-driven: each threshold boundary (3/4, 6/7) maps to the right tier; missing axis raises; axis value 3 or -1 raises.
2. `RUBRIC` completeness — every in-scope taxon has all 5 axes ∈ {0,1,2} and a rationale per axis; `taxon_tier` returns a valid tier for each; unknown slug raises.
3. `species_slug_for_task` — the 11 live task titles parse to expected slugs (incl. genus-only `"Rosa"` → `"rosa"` and the multi-task taxa); empty/malformed title raises.
4. Consistency invariant — `species_slug_for_task(task) == ReconTask.species_slug` for all 5 recon tasks.
5. `materialize_task_difficulty` — each covered task gets a `TaskDifficulty` row whose tier matches its taxon's `TaxonDifficulty`; both tasks of a two-task taxon (e.g. arabidopsis recon + botanical-plausibility) get the **same** tier; idempotent (second run re-upserts, no duplicate rows); a task whose species lacks a `TaxonDifficulty` lands in `skipped` (not raised). The **script** raises when `skipped` is non-empty. Tests run with `commit=False` under transaction rollback; assertions are scoped to the test's own task ids (never global counts).
6. `paradigm_tier_scorecard` — groups by paradigm × tier; means skip `None`; `untiered`/`unspecified` buckets present; `tier_scorecard` output is unchanged (regression).

## Out of scope (deferred follow-on)

- **Taxon expansion** (add fungi + fill thin plant cells, ~3–5 taxa) and the **clean-CC-input sourcing** pass — the specified next build after this de-risks the rubric.
- **Computed corroboration** of the topology/thin-structure/fine-detail axes from GT meshes.
- Any change to the human vote / Bradley-Terry path.

## Success criteria

All 7 in-scope taxa carry a principled, cited, multi-axis tier; every one of their tasks (across every paradigm) inherits it; the `/difficulty` page shows a paradigm × tier grid that operationalizes the recon-degrades / procedural-improves gradient over the existing objective metrics; the existing (tier × generator) scorecard is unchanged; every gate fails loud; full suite green; nothing runs against the real study DB.
