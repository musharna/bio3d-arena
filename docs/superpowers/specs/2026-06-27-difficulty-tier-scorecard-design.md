# Difficulty-Tier Scorecard — Design

> Status: approved (2026-06-27). Scope: the difficulty-tier dimension only.

## Goal

Add a **difficulty-tier dimension** to the benchmark so objective recon accuracy can
be sliced by how hard each task is — answering _"which difficulty tiers break which
generators?"_. The ground-truth scoring infra (`Metric`, `OrganMetric`, `ReconTask`,
AgriGen `/score`) already exists; this design adds the missing tier layer and a
scorecard that aggregates the **existing** scored data by tier.

## Non-goals (deferred)

- Structured difficulty-factor tags (thin_structure / occlusion / …) and multi-factor
  scores. The free-text `rationale` can hold this informally until a factor taxonomy is justified.
- Tier-sliced Bradley-Terry leaderboards (scope-filtered recompute of human/VLM boards).
- Real-plant GT acquisition/wiring (external: needs scans + the AgriGen service up).
- Computed/automatic difficulty. Tiers are manually curated.

## Global constraints

- **Schema is create_all-only — NO ALTER/migration.** New behavior is a new table, never
  a column on an existing one. Mirrors the `ReconTask`/`OrganMetric` convention.
- **Human voting/ranking path is untouched.** No changes to `Vote`/`Rating`/`apply_vote`/
  `api_vote`/`_matches_for_scope`.
- **Honest N/A, never silent drops.** Untiered tasks and unscored outputs are surfaced in
  explicit buckets / rendered `—`, matching `OrganMetric`'s `status` convention.
- Tests are TDD, per unit. SQLite via SQLAlchemy `create_all`.

## Architecture

A dedicated side table for the tier assignment, a pure aggregation module that joins the
existing metric tables to the tier, and two thin read-only consumers (a JSON endpoint and
a markdown report). No write path touches existing tables.

```
TaskDifficulty (new)         Metric / OrganMetric (existing)
      │                              │
      └── task_id ── Task ── ModelOutput ──┘
                         │
                  difficulty.tier_scorecard(db)   →  GET /api/difficulty.json
                                                  →  scripts/difficulty_report.py
```

## Components

### 1. Schema — `TaskDifficulty` (`app/models.py`)

```
__tablename__ = "task_difficulty"
id            int PK
task_id       int FK→task.id, UNIQUE, index   # one tier per task
tier          str(16)                          # ∈ TIERS
rationale     Text default ""                  # why this tier (free text)
updated       datetime default now, onupdate now
```

Unique on `task_id` (`uq_task_difficulty_task`). New table → picked up by `create_all`.

### 2. Tier vocabulary + assignment (`app/difficulty.py`)

- `app/difficulty.py`: `TIERS = ("easy", "moderate", "hard")` and `TIER_ORDER = {t: i …}`
  for canonical sort.
- `set_task_difficulty(db, task_id, tier, rationale="", commit=True) -> TaskDifficulty`
  (in `app/difficulty.py`): validates `tier in TIERS` (raise `ValueError` otherwise),
  validates the task exists, upserts by `task_id`.
- `scripts/assign_difficulty.py`: bulk-assign from a curated in-script map keyed by
  `ReconTask.species_slug` → `(tier, rationale)` (resolves slug→task_id via `ReconTask`).
  Seeds sensible initial tiers for the existing recon tasks, each with a one-line
  rationale; clearly editable. Idempotent (upsert).

### 3. Aggregation — `tier_scorecard(db)` (`app/difficulty.py`)

Returns an ordered structure: for each tier in `TIERS` plus an `"untiered"` bucket, a list
of per-generator rows. Each row aggregates the **existing** `Metric` (+ `OrganMetric`) rows
for that generator's outputs whose task falls in the tier:

```
{
  "tier": "hard",
  "rows": [
    {"generator": "Hunyuan3D 3.1 (Replicate)", "n_outputs": 3, "n_scored": 3,
     "mean_chamfer": 0.041, "mean_fscore": 0.62, "mean_structural": 0.55,
     "species_pass_rate": 0.67},
    ...
  ],
}
```

Rules:

- Group by `(tier, generator)`. A generator with zero scored outputs in a tier still
  appears with `n_scored=0` and `None` means (rendered `—`) — honest, not dropped.
- `mean_*` skip `None`/missing metric rows (a procedural output with no `Metric` row
  contributes to `n_outputs` but not `n_scored`).
- `mean_chamfer`/`mean_fscore` come from `Metric.chamfer`/`Metric.fscore`; `mean_structural`
  from `OrganMetric.botanical_fidelity` (its own table — an output may have one, both, or neither).
- `species_pass_rate` = fraction of scored outputs with `Metric.species_verdict == "PASS"`.
- `untiered` bucket collects outputs whose task has no `TaskDifficulty` row.
- Pure read; no mutation; no BT recompute.

### 4. Surface

- `GET /api/difficulty.json` (`app/main.py`): returns `tier_scorecard(db)` as JSON. Read-only.
- `scripts/difficulty_report.py --date <YYYY-MM-DD>`: writes
  `docs/results/<date>-difficulty-scorecard.md` — one markdown table per tier (+ untiered),
  columns: generator | n | mean chamfer | mean F-score | mean structural | species PASS-rate.
  `None` → `—`. Mirrors `scripts/calibration_report.py`.

## Data flow

curate tiers (`assign_difficulty.py`) → `TaskDifficulty` rows → `tier_scorecard(db)` joins
existing `Metric`/`OrganMetric` by tier → JSON endpoint + markdown report.

## Error handling / edge cases

- `set_task_difficulty` with bad tier → `ValueError`; with unknown task_id → `ValueError`.
- Re-assignment → upsert (no duplicate rows; `updated` bumped).
- Output with no `Metric` row → `n_scored` excludes it; means computed over scored only.
- Tier with no tasks/outputs → present with empty `rows` (honest).
- `mean_chamfer` over zero scored → `None` → `—`.

## Testing (TDD, per unit)

1. `TaskDifficulty` schema round-trip + unique-constraint on `task_id`.
2. `set_task_difficulty`: valid upsert; invalid tier raises; unknown task raises; re-assign updates.
3. `tier_scorecard`: correct grouping + means on a seeded fixture (2 tiers × 2 generators,
   mix of scored/unscored outputs); untiered bucket captures unassigned tasks; species_pass_rate.
4. `difficulty_report.py`: renders a table per tier, `None`→`—`, file written to the dated path.
5. `GET /api/difficulty.json`: returns the scorecard shape; read-only (no rows written).
6. Regression: human vote path + existing leaderboards unchanged (smoke import + existing suite green).
