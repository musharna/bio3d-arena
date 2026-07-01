# Procedural code-gen scorecard (`/procedural`) — Design Spec

> Created 2026-07-01. Parent: bio3d-arena, first per-paradigm track sub-project on top of the
> multi-paradigm Foundation. Surfaces the commissioned procedural_llm arena (LLMs authoring
> Blender-Python) as a first-class, named scorecard.

## Problem

The commissioned-generation arena produced real, agent-attributed plant models and scored them
(execution outcomes in `CommissionAttempt`; morphology verdicts in `TraitVerdict`), but the
results live only in DB rows + a memory file. There is no surfaced board — the session's novel
contribution (a code-generation 3D arena) is invisible in the app.

## Goal

A dedicated `/procedural` page showing a per-model scorecard for the `procedural_llm` paradigm,
built entirely from existing data (no new generation/spend, no new tables): execution
reliability (pass@1) + botanical morphology fidelity (experimental). Named models, ranked by
the objective axis.

## Decisions (locked in brainstorming)

1. Metrics = **existing data only**: pass@1 + morphology fidelity. No pass@k, no vote board.
2. Surface = **dedicated `/procedural` page** (a distinct board type — objective code-gen
   metrics, not vote-based BT — so it does not share the leaderboard's columns).
3. **Named models** (not anonymized): the point is comparing known LLMs, like LMArena's
   revealed leaderboard.
4. **Rank by pass@1** (objective); morphology fidelity is a labeled secondary column.
5. Morphology fidelity is **explicitly experimental** (Mode-C κ-gate is open → the VLM judge
   is uncalibrated); the page must say so.

## A. Aggregation (read-only)

New `service.procedural_scorecard(db) -> list[dict]`, one row per generator whose
`paradigm == "procedural_llm"`, each with:

- `model`: the generator's display name / model_id (named).
- `attempts`: count of `CommissionAttempt` rows for that generator's `model_id`.
- `valid`: count with `status == "ok"`.
- `pass_at_1`: `valid / attempts` (0.0 when attempts == 0).
- `morph_correct`, `morph_assessable`: over the generator's commissioned `ModelOutput`s'
  `TraitVerdict`s, count `verdict == "present_correct"` and count scope-assessable
  non-`not_assessable` verdicts. Assessability mirrors existing scoring (`service.py:771`)
  exactly: `is_assessable(scopes.get(output_id), {"key": v.trait_key, "trait_class":
v.trait_class})` with `scopes = service.load_scopes(db)`.
- `morph_fidelity`: `morph_correct / morph_assessable` (None when morph_assessable == 0).
- `median_verts`: median of `json.loads(a.mesh_stats_json).get("vertices", 0)` over the
  generator's `status == "ok"` `CommissionAttempt`s (0 when none). Key is `"vertices"`
  (verified: `commission.py:125`).
- `n`: alias for attempts (context).

Rows sorted by `pass_at_1` desc, tiebreak `morph_fidelity` desc (None last).

**Linking keys (verified against live models):** attempts join to their generator via
`CommissionAttempt.generator_id` (the attempt stores it directly — do NOT match on the
`model_id` string). Outputs join via `ModelOutput.generator_id` filtered to
`source == "commissioned"`. Only generators with `paradigm == "procedural_llm"` appear.

## B. Page + routes

- `GET /procedural` (HTMLResponse) → renders `app/templates/procedural.html` with the scorecard
  rows + a header explaining the paradigm.
- `GET /api/procedural.json` → returns the scorecard rows as JSON.
- Nav: add a "Procedural" link in `base.html` nav alongside the existing leaderboard/coverage links.

Columns rendered: **Rank · Model · pass@1 (valid/attempts) · Morphology fidelity %
(experimental) · Median verts · n**. Fidelity cell shows "—" when `morph_fidelity is None`.

## C. Copy / framing

Page header: "Procedural code-gen arena — each model authors a Blender-Python script that must
run headless (Blender 4.2) and produce a valid plant mesh. **pass@1** = share of the 6 tasks
that ran and yielded a valid mesh. **Morphology fidelity** = share of judgeable traits the model
got right — **experimental / uncalibrated** (the VLM judge has not passed the Mode-C κ-gate);
treat it as a relative signal, not certified accuracy."

## Testing

- `procedural_scorecard`: fixture with 2 procedural_llm generators — one with 2/2 ok attempts,
  one with 1/2 — plus a few TraitVerdicts (mix of present_correct / present_wrong / absent /
  not_assessable) on their commissioned outputs with ModelScope rows → assert pass@1 values,
  morph_fidelity (present_correct / assessable, na excluded, scope-unassessable excluded), rank
  order (higher pass@1 first). Non-procedural_llm generators are excluded. Empty DB → [].
- Route: `GET /procedural` → 200 and contains a known model name; `/api/procedural.json` → rows
  list with the documented keys.
- Full suite stays green.

## Risks / non-goals

- Fidelity is uncalibrated — mitigated by explicit labeling; not a certified metric.
- Small n per model (6 tasks) — the page shows raw counts (valid/attempts) so sample size is
  visible, not hidden behind a percentage.
- Not in scope: pass@k, VLM-judge vote board, texturing, cross-paradigm comparison, new tables.
