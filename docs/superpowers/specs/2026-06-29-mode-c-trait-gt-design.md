# Mode-C: Botanical-Trait Ground Truth — Design

> **Date:** 2026-06-29
> **Status:** Approved design (pre-plan). Next step: `writing-plans` → implementation plan.
> **Author:** Taxon3D
> **Context:** First sub-project of the "scale program" (competitive audit #4). The other
> sub-projects — A: phylogenetic coverage/difficulty framework, C: LLM-as-generator modality,
> D: long-tail acquisition + photo galleries — get their own specs. See
> `~/.claude/projects/-home-mjarnold-bio3d-arena/memory/competitive_audit_2026-06-29.md`.

## Goal

Add a third, **objective** evaluation axis ("Mode-C") that grades a generated 3D model
against a per-taxon **botanical-trait rubric** sourced from the literature. A VLM checks each
visually-assessable trait against the rendered model and returns a per-trait verdict; the
aggregate is a **botanical-accuracy score**.

Mode-C exists because it solves the problem that blocks the whole scale program: **the long
tail has no 3D ground-truth scans.** A trait rubric (size, spadix shape, color, habit…) exists
in the literature even when no scan does, so Mode-C makes rare taxa scoreable. It is also
**scale-invariant by construction** (scores traits, not mesh distances), sidestepping the
mesh-size confound that dominates raw-Chamfer comparisons across size-heterogeneous taxa.

Secondary benefit: the structured trait database is **dual-use** — it is exactly the
machine-readable plant-trait knowledge base AgriGen wants.

## Scope

**In scope (this spec):**

- Mode-C for **plants**, prototyped and calibrated on the **6 existing recon species**
  (tomato, maize, pine, rose, soybean, arabidopsis), designed to extend to new taxa.
- Trait-rubric data model + authoring pipeline (hybrid sourcing with provenance).
- VLM trait-checking pipeline (forced-tool, resumable), reusing the existing render + judge infra.
- Per-trait-class κ calibration + acceptance gate.
- Per-output trait scorecard, generator-level Mode-C leaderboard, a Mode-C column on
  `/coverage` and the difficulty view, and JSON export.

**Non-goals (separate sub-specs):**

- Cross-kingdom traits (fungi/animals/microbes).
- Long-tail taxon acquisition + voter-facing photo galleries (axis D).
- LLM-as-generator modality (axis C).
- Phylogenetic taxonomy backbone / stratified sampling / per-clade map (axis A) — Mode-C will
  _feed_ that map later, but does not build it here.
- Absolute-size and non-visual traits (chromosome count, phenology) — deliberately excluded.

## Background & key decisions

Resolved during brainstorming (2026-06-29):

1. **Trait source = hybrid with provenance tiers.** Structured DB backbone (Kew POWO /
   Wikidata / TRY) for citable traits + LLM enrichment for visual specifics. Every trait
   carries `source_tier` (`db` | `llm`) and a citation. `db`-tier is trusted; `llm`-tier is
   spot-checked before going live. (Counters ghost-traits; honours the citation discipline.)
2. **Scored trait classes = visual + relative, no absolute size.** Score habit/architecture,
   organ shape, phyllotaxy, inflorescence type, color, presence/absence, and scale-invariant
   relative proportions. Exclude absolute size (re-introduces the mesh-size landmine) and
   non-visual traits. The VLM may also return `not_assessable` as a runtime safety net.
3. **Calibration = per-trait-class κ + acceptance gate.** Human labels rubric traits on a
   sample; Cohen's κ is computed per trait class; only classes clearing **κ ≥ 0.6** count
   toward the score. Uncalibrated classes are shown in scorecards but excluded from score/board.
4. **Surfacing = full axis.** Per-output scorecard + generator-level board (calibrated classes
   only) + a Mode-C column on `/coverage` and the difficulty map. Objective, so no vote-volume
   wait. Per-output scorecards are the interpretable payload ("missed the spadix").

Defaults (approved):

- AgriGen = **loose coupling** (JSON export, no shared DB).
- Prototype on the **6 recon species**.
- Rubric **keyed per task/taxon** (one rubric per species task) initially.
- κ acceptance bar = **0.6** (substantial agreement; tunable).

## Architecture overview

```
literature / structured DBs
        │  build_trait_rubrics.py  (hybrid + provenance)
        ▼
   TraitRubric  ──────────────────────────────────────────────┐
        │                                                       │ export
        │  trait_judge.py (render multi4 → VLM forced-tool)     ▼
        ▼                                          /api/traits.json  (AgriGen)
   TraitVerdict (per output × trait)
        │  recompute_trait_scores (calibrated classes only)
        ▼
   TraitScore (per output)  ──►  Mode-C board, /coverage col, difficulty map
        ▲
   TraitCalibration (per class: κ, accepted)   ◄── human trait-labels + cohens_kappa
```

Reuses, verified against live code:

- `app/judge.py` — `JUDGE_MODEL = "claude-sonnet-4-6"`, `judge_pair(...)` forced-tool pattern
  (`tool_choice={"type":"tool","name":...}`). Mode-C adds an analogous `record_traits` tool.
- `app/judge_render.py` — `render_contact_sheets(...)`, `CONDITIONS["multi4"]`, contact-sheet
  caching. Mode-C reuses the multi4 sheet (now also hardened to surface render failures).
- `app/calibration.py` — `cohens_kappa(labels_a, labels_b)`; `human_vs_judge_kappa` is the
  template for the per-class calibration query.
- `app/models.py` — `Metric` / `Critique` are the per-output-table precedent; create_all-only
  (no migrations), flags/free-form in `meta_json`.
- `scripts/judge_vlm.py` — resumable batch driver (swap-group skip); `trait_judge.py` mirrors
  its resumability + dry-run + `--max` discipline.

## Data model (new tables; create_all-only)

### `TraitRubric`

| field             | type       | notes                                                              |
| ----------------- | ---------- | ------------------------------------------------------------------ |
| id                | int PK     |                                                                    |
| taxon             | str        | scientific name (e.g. "Solanum lycopersicum")                      |
| task_id           | FK task.id | the species task this rubric attaches to (nullable for taxon-only) |
| traits_json       | Text(JSON) | list of trait objects (below)                                      |
| created / updated | datetime   |                                                                    |

Trait object schema (inside `traits_json`):

```json
{
  "key": "inflorescence_type",
  "trait_class": "inflorescence", // habit|organ_shape|phyllotaxy|inflorescence|color|presence|proportion
  "type": "categorical", // categorical|presence|proportion
  "expected": "cyme", // expected value / allowed set / ratio range
  "visual": true, // always true for scored traits
  "source_tier": "db", // db | llm
  "citation": "POWO 2026: Solanum lycopersicum"
}
```

### `TraitVerdict` (per output × trait — `JudgeVote` analog)

| field       | type                        | notes                                                                |
| ----------- | --------------------------- | -------------------------------------------------------------------- |
| id          | int PK                      |                                                                      |
| output_id   | FK model_output.id, indexed |                                                                      |
| rubric_id   | FK trait_rubric.id          |                                                                      |
| trait_key   | str                         | matches a trait `key` in the rubric                                  |
| trait_class | str                         | denormalized for fast per-class aggregation                          |
| verdict     | str                         | `present_correct` \| `present_wrong` \| `absent` \| `not_assessable` |
| rationale   | Text                        | VLM's one-line justification                                         |
| judge_model | str                         | provenance                                                           |
| created     | datetime                    |                                                                      |

Uniqueness: `(output_id, trait_key, judge_model)` — resumable skip key.

### `TraitScore` (per output — `Metric` analog)

| field              | type                                | notes                                                     |
| ------------------ | ----------------------------------- | --------------------------------------------------------- |
| id                 | int PK                              |                                                           |
| output_id          | FK model_output.id, unique, indexed |                                                           |
| botanical_accuracy | float\|null                         | satisfied ÷ (assessable traits in **calibrated** classes) |
| n_scored           | int                                 | denominator actually used                                 |
| n_total            | int                                 | traits in rubric (for context)                            |
| judge_model        | str                                 |                                                           |
| updated            | datetime                            |                                                           |

"Satisfied" = `present_correct`. `not_assessable` and traits in uncalibrated classes are
excluded from both numerator and denominator.

### `TraitCalibration` (per trait class — the gate)

| field       | type        | notes                                     |
| ----------- | ----------- | ----------------------------------------- |
| id          | int PK      |                                           |
| trait_class | str, unique |                                           |
| kappa       | float\|null | human↔VLM Cohen's κ on the labeled sample |
| n           | int         | sample size                               |
| accepted    | bool        | `kappa >= 0.6 and n >= MIN_N`             |
| updated     | datetime    |                                           |

## Pipeline

### 1. Rubric authoring — `scripts/build_trait_rubrics.py`

- Input: a taxon list (the 6 recon species for the prototype).
- Backbone: pull structured traits from POWO/Wikidata/TRY (db-tier, citation = source record).
- Enrichment: LLM drafts additional **visual** traits (habit, inflorescence shape, color,
  distinctive organs) constrained to the scored classes, each emitted with a citation and
  `source_tier="llm"`.
- Output: one `TraitRubric` per taxon. `llm`-tier traits are flagged for spot-check; a small
  human review confirms a sample before they're allowed to count toward scores.
- Discipline: no trait without a citation; absolute-size/non-visual traits dropped at authoring.

### 2. Trait checking — `scripts/trait_judge.py`

- For each (output, its task's rubric): ensure the multi4 contact sheet exists
  (`judge_render.render_contact_sheets`, now failure-surfacing), then one VLM call with a
  forced `record_traits` tool: input = sheet + the rubric's scored traits; output = per-trait
  `{verdict, rationale}`. Persist `TraitVerdict` rows.
- Resumable (skip existing `(output_id, trait_key, judge_model)`); `--dry-run` count + `--max`
  cap before any spend, exactly like `judge_vlm.py`.
- Excludes reference-scan / untextured outputs (not meaningful to trait-check) via the existing
  `sourcing` predicates.

### 3. Calibration — human labels + `cohens_kappa`

- Sample outputs across taxa; a human labels each scored trait (same verdict vocabulary).
- For each trait class, `calibration.cohens_kappa(human_labels, vlm_labels)` → write
  `TraitCalibration` (`accepted = kappa >= 0.6 and n >= MIN_N`).
- Re-runnable as more labels accrue.

### 4. Scoring — `service.recompute_trait_scores(db)`

- Per output: `botanical_accuracy = |present_correct in calibrated classes| ÷ |assessable in
calibrated classes|`; write `TraitScore`.
- Generator-level board: mean `botanical_accuracy` over a generator's scored outputs (recon-
  board style; reuse the display-name + exclusion helpers).

## Surfacing

- **Per-output trait scorecard** — `/trait/<output_id>` (and embedded on spotlight detail):
  table of trait → expected → verdict → rationale, with source citations and a
  calibrated/uncalibrated marker per row.
- **Mode-C leaderboard** — generator botanical-accuracy board (calibrated classes only),
  alongside the Mode-B recon board on `/benchmark` (or a sibling section).
- **`/coverage`** — add a "Mode-C" column (per-task: rubric present? n traits? mean accuracy?).
- **Difficulty view** — add per-tier mean botanical-accuracy (and later per-clade, feeding
  axis A's map).
- **Exports** — `/api/traits.json` (rubrics + provenance) and `/api/trait_scores.json`
  (per-output + per-generator), for AgriGen and external audit.

## Trust & honesty

Mode-C is labeled **experimental until its trait classes pass the κ-gate**. Uncalibrated
classes appear in scorecards (for transparency) but are excluded from `botanical_accuracy`, the
board, and the difficulty map. This mirrors the calibrate-before-trust discipline that makes the
existing VLM judge defensible (see the eval-loop calibration study).

## AgriGen integration

Loose coupling only. Taxon3D owns the trait tables and exposes `/api/traits.json` +
`/api/trait_scores.json`. AgriGen consumes the export; there is **no shared database** and no
cross-project import dependency. (A tighter integration can be a later, separate decision.)

## Risks & mitigations

| Risk                                           | Mitigation (designed in)                                                                                             |
| ---------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| **Ghost-traits** (LLM invents traits)          | Provenance tiers + citation on every trait; llm-tier spot-checked; db-tier preferred.                                |
| **VLM trait-blindness** (can't see phyllotaxy) | Per-class κ gate; blind classes are excluded from the score, not silently trusted.                                   |
| **Mesh-size confound**                         | Absolute-size traits excluded by design; only scale-invariant classes scored.                                        |
| **Rubric coverage gaps** on the long tail      | `n_scored`/`n_total` surfaced; thin rubrics visible, not hidden; Mode-A+gallery remains the fallback axis.           |
| **Render failures** poisoning verdicts         | Reuse the hardened `render_contact_sheets` (surfaces failures); trait_judge skips outputs whose sheet didn't render. |
| **API spend**                                  | `--dry-run` count + `--max` cap before any batch, per the judge_vlm discipline.                                      |

## Testing strategy

- **Unit:** rubric schema validation (required fields, citation present, scored-class only);
  `recompute_trait_scores` aggregation (calibrated-class + `not_assessable` filtering; correct
  denominator); κ-gate logic (`accepted` boundary); export shape.
- **Route smoke:** `/trait/<id>`, Mode-C board, `/coverage` Mode-C column, the two JSON exports.
- **Real-execution check (required):** run `trait_judge.py` on 1–2 real outputs against a real
  rubric and eyeball the per-trait verdicts before trusting the pipeline — pair the synthetic
  unit tests with one live VLM call, per the real-execution-testing doctrine.

## Rollout / phasing

1. Data model + `recompute_trait_scores` + tests (no VLM).
2. `build_trait_rubrics.py` for the 6 recon species (db backbone + llm enrichment + provenance).
3. `trait_judge.py` (dry-run first) → verdicts for existing outputs.
4. Calibration pass (human labels → κ → gate).
5. Surfacing (scorecard, board, coverage/difficulty columns, exports).
6. Flip Mode-C from "experimental" to live for the classes that pass the gate.

## Open questions (non-blocking)

- Exact structured-DB endpoints/fields for POWO/Wikidata/TRY trait pulls (resolve in the plan).
- `MIN_N` for a trait class to be gate-eligible (start ~20; tune with data).
- Whether the Mode-C board lives on `/benchmark` or its own page (UI decision, defer to plan).
