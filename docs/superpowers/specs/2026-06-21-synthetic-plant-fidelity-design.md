# Synthetic-Plant Botanical Fidelity — Design Spec

> Taxon3D · 2026-06-21 · the fast-follow vertical from the benchmark-landscape triage
> (`~/.claude/projects/-home-mjarnold-agrigen/memory/bio_benchmark_vertical_landscape_2026-06-20.md`).
> Pure greenfield: nobody scores "is this _generated_ plant botanically plausible?".

## Goal

A living, arena-style leaderboard that ranks **3D-plant generators** by **botanical plausibility**
— "which generated plant looks more like a real plant?" — via anonymous pairwise human/VLM votes.
This is the subjective-judgment counterpart to the recon benchmark's objective accuracy track
(the cross-cutting law: vote form fits subjective/contested judgment; the recon track already
covers the GT-scorable half).

## Two-mode placement

- **Mode A (votes) only.** Botanical plausibility has **no ground-truth scalar** to chamfer against
  — it's a perceptual, multi-criteria judgment. So this vertical is votes-only (decision confirmed
  in brainstorming). Objective botanical-trait metrics (phyllotaxy/branching/leaf geometry via
  AgriGen `quality_metrics`) are a possible later Mode-B add — **out of scope here**.
- The Turing-style framing (mix in real plants, "real or generated?") is also **deferred**.

## The key design property: it's a SCOPE REUSE, not a new subsystem

bio3d-arena's schema is deliberately generic (`Category` / `Criterion` / `Task` / `Generator` /
`ModelOutput` are all DB rows). A new vertical is a **row-insert**, not a schema change. The entire
voting → Bradley-Terry → leaderboard → significance pipeline is **already per-(category × criterion)
scoped**. So this vertical reuses:

- **Matchmaking + arena** (`matchmaking.pick_task`/`pick_pair`, `/api/next`, `/api/vote`) — serves
  pairs from the new category, scoped by URL params (the Mode-A wiring already added URL-scoping).
- **Ranking** (`service.recompute_all`, `ranking.bradley_terry`, `significance_matrix`).
- **Leaderboard + significance pages** — `/leaderboard?category=synthetic-plants&criterion=botanical_plausibility`
  and `/significance?...` already render any scope.
- **Ingest** (`ingest.register_output`, the viewer registry for GLB).

**No new page. No new scorer. No new ranking code.** This is the YAGNI win the generic schema was
kept for.

## Architecture (what's actually new)

1. **Category** `synthetic-plants` ("Synthetic Plants" — procedurally/AI-generated 3D plants).
2. **Criterion** `botanical_plausibility` ("Which looks more like a botanically real plant?").
   Distinct from the existing `realism`/`overall` so synth-plant votes don't mix with recon votes.
3. **Tasks** — one per plant TYPE (e.g. "A botanically plausible date palm", "…maize", "…tomato",
   "…arabidopsis", "…Scots pine"), so plausibility is judged _within_ a type (apples-to-apples).
   Start with the types we have assets for.
4. **Generators** — the competing generation paradigms, ingested as `ModelOutput`s per task:
   - `pd-archetype` — AgriGen's procedural-descriptor canonical-form generator (the on-mission
     synthetic source).
   - the recon methods (`trellis`, `hunyuan3d`, `instantmesh`) — image-reconstructed plants are
     also "generated"; reusing them gives a **cross-paradigm** first matchup (_is a built plant more
     botanically plausible than a reconstructed one?_) with assets that already exist.
     Each task needs ≥2 generator outputs to be votable; the cross-paradigm mix supplies that.
5. **`seed_synthetic_plants(db)`** (mirrors `seed_recon_benchmark`, idempotent) — creates the
   category, criterion, per-type tasks, and the generator rows. Added to `seed_all` + the
   force-reseed delete-cascade as needed (Category/Criterion already in the cascade).
6. **Ingest path** — a small script (mirrors `scripts/ingest_bakeoff.py`) registering the procedural
   -plant GLBs onto their type tasks under `pd-archetype`; recon entries reuse the existing recon
   GLBs (registered onto the synth-plant tasks under the recon-method generators).

## Data flow

```
AgriGen PD-archetype GLB ─┐
recon-method GLBs ────────┼─ ingest.register_output ─▶ ModelOutput (synthetic-plants tasks)
                          ┘
arena (/api/next?category=synthetic-plants&criterion=botanical_plausibility)
   → pairwise vote → Comparison/Vote → recompute_all → Rating (scope-cached)
   → /leaderboard + /significance (scoped) = the plausibility ranking
```

## Asset dependency

The procedural-plant GLBs are produced by **AgriGen** (the PD archetype system — their lane, like the
recon harvest). The bio3d-arena side ships the scaffolding + ingest path; the board fills as AgriGen
exports archetype GLBs. To validate the plumbing immediately (no wait), the cross-paradigm seed uses
the **existing recon GLBs** as the recon-method entries, so the arena is votable on day one.

## Error handling

- Same as recon ingest: GLB validated at register (`ingest.validate_3d_asset`); a bad asset is
  skipped, not fatal.
- A task with <2 outputs simply isn't served by matchmaking (existing `pick_task` guard) — no error.

## Testing

- `seed_synthetic_plants` idempotency (re-run → no duplicate category/criterion/tasks/generators);
  the category/criterion/tasks exist with the right slugs.
- Matchmaking serves a synthetic-plants pair scoped to `botanical_plausibility` (reuse the existing
  arena test pattern).
- Vote → recompute → the scoped `/leaderboard` ranks the generators (reuse the Mode-A loop test).
- Real-execution check: ingest a couple of real GLBs onto a type task, cast scoped votes via the API,
  recompute, confirm the scoped leaderboard returns a ranked board.

## Global constraints

- Pure scope-reuse; no new page/scorer/ranking code. New code = `seed_synthetic_plants` + an ingest
  script + tests.
- Test env `.venv/bin/python -m pytest`; ruff clean.
- Asset production (procedural plants) is AgriGen's lane — do not build generators here.

## Out of scope (deferred)

- Objective botanical-trait metrics (Mode-B for synth plants) — a later dual-mode add.
- Turing-style real-vs-generated condition.
- A dedicated `/synthetic` landing page (the scoped `/leaderboard` suffices for the MVP).
