# Plant Input Advisor — Design

> Status: approved (2026-06-27). Scope: an **offline** advisor that maps each recon subject's plant
> growth form to a capture-photo recipe + recon strategy, and grades the current reference photo
> against that recipe. Motivated by the multi-view e2e caveat (top-down rosette views make the recon
> droop) and the earlier "bad field photo" failures — turn those lessons into a reusable per-subject
> recipe + input check.

## Goal

Given each recon subject (species), produce: (1) its **growth form** classification, (2) the
**recommended capture recipe + recon strategy** for that form (single vs multi-view, NVS pose hints,
known failure modes), and (3) a **grade of the current reference photo** against that recipe
(pass / marginal / reject, with reasons). Output is an offline report + a durable per-species
morphology record. The advisor recommends; a human acts on it.

## Non-goals (out of scope)

- Auto-wiring the advisor into the recon spend path (`generate_*_recon`) — advisory only for now.
- A learned/trained classifier — premature (6 subjects, objective scorer down). Hand-curated seed +
  zero-shot VLM instead.
- UI surfacing in arena/spotlight — offline CLI/report only.
- Fixing recon model fidelity (e.g. the droop itself) — the advisor _flags_ it, doesn't fix it.
- CV saliency / background removal — research showed it fails on these low-contrast subjects.

## Global constraints

- `ANTHROPIC_API_KEY` from env, **never logged/pasted** (mirrors `scripts/judge_vlm.py`); exception
  text carries exception _type names_ only, never key material.
- Schema is **create_all-only** — NEVER ALTER/migrate. New table created via the existing metadata.
- Human voting/ranking path and existing single-image / multi-view recon remain untouched.
- Honest N/A: missing photo, no key, or a VLM error is **skipped + logged**, never faked.
- Reuse the existing VLM call pattern (`app/judge.py`). Key subjects by the `CROPS` subject slug
  (short form, e.g. `arabidopsis`, `pinus`; = the `reference/<slug>_ref.jpg` filename stem) — NOT the
  `ReconTask.species_slug` binomial (`arabidopsis_thaliana`), a separate namespace.

## Architecture

```
subject (subject_slug, e.g. "arabidopsis")
  → PlantMorphology row (growth_form; hand-curated seed, idempotent upsert)   [app/morphology.py + DB]
  → STRATEGY[growth_form]  (capture recipe + recon_mode + nvs hint + expected_failure)  [code constant]
  → grade current reference photo against the recipe                          [app/input_grade.py]
       ├─ deterministic heuristics (PIL): dims ≥1024, bg corner uniformity
       └─ VLM grader (claude-sonnet-4-6, forced tool): growth_form, bg/view/fill, verdict, reasons
  → markdown (+ optional JSON) report                                         [scripts/advise_inputs.py]
```

The DB stores only the per-species **classification**; the **rules** (recipe per growth form) live in
code, so they are versioned and testable and never duplicated per species. The grader checks a photo
against the rule entry for _its_ growth form (so a rosette is not penalized for being top-down).

## Components

### 1. Data model — `PlantMorphology` (`app/models.py`)

Lean table, create_all-only:

| column         | type                        | meaning                                          |
| -------------- | --------------------------- | ------------------------------------------------ |
| `id`           | Integer PK                  |                                                  |
| `subject_slug` | String(64), unique, indexed | `CROPS` subject slug (= `reference/<slug>` stem) |
| `growth_form`  | String(32)                  | one of the taxonomy values                       |
| `notes`        | Text, nullable              | per-species nuance / override rationale          |
| `updated`      | DateTime                    | last upsert                                      |

### 2. Taxonomy + STRATEGY table (`app/morphology.py`)

**Growth-form taxonomy** (string constants; the 6 needed + 2 reserved for extensibility):
`ROSETTE`, `ERECT_HERB`, `GRAMINOID`, `SHRUB`, `TREE_CONIFER`, `VINE_SPRAWLING`,
(reserved: `TREE_BROADLEAF`, `SUCCULENT`).

**`STRATEGY: dict[str, StrategyEntry]`** — one entry per growth form. `StrategyEntry` fields:
`capture_view` (str), `background` (str), `framing` (str), `min_px` (int, default 1024),
`recon_mode` (`"single" | "multiview" | "multiview_preferred" | "multiview_required"`),
`nvs_pose_hint` (str), `expected_failure` (str).

Content (encodes observed learnings incl. the e2e caveat):

| growth_form    | capture_view                     | recon_mode                | nvs_pose_hint / caveat                                                                                                                                |
| -------------- | -------------------------------- | ------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| ROSETTE        | top-down (radially flat)         | single                    | multi-view droops: top-down NVS gives no flat-ground constraint → leaves cascade down (observed e2e). If multi-view, bias NVS to side/mid elevations. |
| ERECT_HERB     | ¾ / front, full height           | single (multi-view helps) | default NVS poses fine                                                                                                                                |
| GRAMINOID      | front, full height               | multiview_preferred       | thin vertical blades lost single-image; needs lateral views                                                                                           |
| SHRUB          | ¾ view                           | single (dense bloom)      | multi-view recovers occluded interior                                                                                                                 |
| TREE_CONIFER   | front, full tree                 | multiview_required        | single-image blobs needles (confirmed pine); even multi-view hard — flag low confidence                                                               |
| VINE_SPRAWLING | isolate a representative section | multiview                 | sprawling habit hard to frame as one subject                                                                                                          |

Shared recipe (all forms): plain/neutral background; subject centered & >50% of frame; ≥1024px; soft
even light.

**Seed** (`seed_morphology(db)`): idempotent upsert of the 6 recon subjects:
arabidopsis→ROSETTE, maize→GRAMINOID, soybean→ERECT_HERB, tomato→ERECT_HERB
(note: indeterminate field tomatoes are vining; our reference is a potted front-on specimen),
rose→SHRUB, pinus→TREE_CONIFER.

### 3. Grader (`app/input_grade.py`)

`grade_input(image_bytes, *, growth_form, strategy_entry, client=None, heuristics_only=False)
-> GradeResult`.

- **Heuristics (PIL, deterministic):** `min(w,h) >= strategy_entry.min_px`; aspect-ratio sanity;
  `bg_uniformity` = colour variance across the 4 corner regions (low ⇒ plain bg = good).
- **VLM grader (reuses `judge.py` forced-tool pattern, `claude-sonnet-4-6`):** single-image call;
  prompt includes the recipe text for `growth_form`; forced tool returns
  `{growth_form (enum), background_ok (bool), view_matches_recipe (bool), fill_ok (bool),
verdict ("good"|"marginal"|"reject"), reasons (str)}`. Pure function, injected client (fake in tests).
- `GradeResult` aggregates: dims, dims_ok, bg_uniformity, vlm verdict block (or `None` when
  `heuristics_only`), `growth_form_match` (vlm vs seed), `verdict`, `reasons: list[str]`.
- `heuristics_only=True` (or no client/key) → deterministic subset only, `verdict` from heuristics.

### 4. CLI + report (`scripts/advise_inputs.py`)

- Flags: `--subject <slug>` (default all 6), `--heuristics-only`, `--refresh`, `--json`.
- Per subject: take the `subject_slug` (a `CROPS` key) → load/seed `PlantMorphology` →
  `STRATEGY[growth_form]` → locate `reference/<slug>_ref.jpg` → `grade_input(...)`.
- `ANTHROPIC_API_KEY` from env (never logged); absent ⇒ auto `heuristics_only` + report note.
- Output: markdown report → `docs/results/YYYY-MM-DD-input-advisor.md` (+ optional JSON); summary
  table to stdout. Per subject: growth form, `recon_mode`, capture recipe, `expected_failure`, and the
  current photo's grade (verdict + reasons + heuristic dims/bg + whether VLM classification matches seed).

## Data flow

`subject_slug` → `PlantMorphology.growth_form` (seed/load) → `STRATEGY[growth_form]` →
`grade_input(ref photo)` → `GradeResult` → report row. `PlantMorphology` upserted (idempotent).

## Error handling / edge cases

- Missing reference photo → skip + log that subject; others continue.
- No `ANTHROPIC_API_KEY` → heuristics-only, noted in report.
- VLM call error → record `vlm_error: <ExceptionType>` (type name only, key-safe); heuristics still
  reported.
- VLM `growth_form` ≠ seed → **flag in report, never auto-overwrite** (human decides).
- Unknown subject (not in `CROPS`) → skip + log.
- Re-run → idempotent upsert; `--refresh` re-grades.

## Testing (TDD, per unit)

1. **morphology:** every `growth_form` has a `STRATEGY` entry; `seed_morphology` populates the 6
   subjects and is idempotent (re-seed = no duplicate rows; updates in place).
2. **input_grade heuristics:** synthetic images — a ≥1024 plain-corner image passes; a small / busy-bg
   image flags; `heuristics_only=True` returns a `GradeResult` with no client and no VLM block.
3. **VLM grader:** a fake client returning a canned tool-use parses into a `GradeResult`; a
   classification that disagrees with the seed sets `growth_form_match=False`.
4. **CLI:** monkeypatched grader + temp DB + a stub reference photo → report written,
   `PlantMorphology` upserted, a missing-ref subject skipped-and-logged, no-key path → heuristics-only
   note in the report.
5. **Regression:** human voting path + existing single-image / multi-view recon untouched; full suite
   green.

Real-execution check (plan, key-gated, deferred like #21): one live VLM grade on the real arabidopsis
reference photo → confirms it classifies `ROSETTE` and returns a verdict, before trusting the grader
across all subjects.
