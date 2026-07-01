# Multi-Paradigm Arena — Foundation Design Spec

> Created 2026-07-01. Parent: bio3d-arena. Sub-project #1 of "integrate all 3D-creation
> paradigms". Enables per-paradigm tracks (image-recon, capture, LLM-procedural,
> expert-procedural, retrieval, and future text-to-3D / video / texturing / agentic) by making
> **paradigm** a first-class dimension and guaranteeing outputs are only ever compared
> within their paradigm.

## Problem

bio3d-arena already contains outputs from at least five distinct 3D-creation paradigms
(image→3D reconstruction, real scans/capture, LLM-written procedural code, expert/simulation
procedural engines, and retrieved library assets), but they share one undifferentiated pool.
Matchmaking pairs any two outputs of a task regardless of paradigm, so Bradley-Terry ratings
mix, e.g., a real scan against an LLM-authored mesh — a category error that confounds every
ranking (the same class of confound that sank Mode-C calibration). There is no way to tag,
separate, or rank within a paradigm.

## Goal

Make **paradigm** a first-class attribute and enforce **within-paradigm-only** comparison and
ranking, backfilling every existing output into its paradigm, with a minimal UI to see it.
This is the enabling layer; each paradigm's richer track (and paradigm-specific metrics) is a
later sub-project. Explicitly out of scope here: procedural pass@k, morphology fidelity,
texturing, new generators, per-paradigm landing pages.

## Decisions (locked in brainstorming)

1. **Tag lives on `Generator`** (each generator is paradigm-consistent; outputs inherit).
2. **Guardrail at BOTH matchmaking and rating aggregation** (pairs are born same-paradigm;
   score-tallying counts only within-paradigm comparisons).
3. **Minimal UI**: a paradigm column + filter on the existing leaderboard and `/coverage`
   views; no new pages.

## A. Data model

Add to `app/models.py` `Generator`:

- `paradigm: Mapped[str] = mapped_column(String(32), default="", index=True)`

Additive via `create_all` (no migration tooling). Paradigm vocabulary (a module-level
constant, e.g. `app/paradigms.py: PARADIGMS`):

- **Used at backfill (present in data):** `image_recon`, `capture_scan`, `procedural_llm`,
  `procedural_expert`, `retrieval`.
- **Reserved (stable enum, not yet populated):** `text_native`, `video`, `texturing`,
  `agentic`, `sketch`.

A helper `is_valid_paradigm(p) -> bool` and the canonical ordered list live in
`app/paradigms.py` so UI, backfill, and guardrail share one source of truth.

## B. Backfill

`scripts/backfill_paradigms.py` — classify every existing `Generator` into a paradigm by
source family, then set `Generator.paradigm`. Mapping (by generator slug / `kind` / the
`source` of its outputs):

| paradigm            | generator family (match rule)                                      |
| ------------------- | ------------------------------------------------------------------ |
| `procedural_llm`    | slug starts `openrouter-` (commissioned)                           |
| `procedural_expert` | L-Py / lpy / Infinigen authored generators                         |
| `image_recon`       | recon-API generators (hunyuan, tripo, partcrafter, meshy, `api:*`) |
| `capture_scan`      | ICRISAT / ROMI / reference-scan sources                            |
| `retrieval`         | sketchfab / objaverse sources                                      |

- **Fail loud**: if any generator matches no rule, the script raises and lists the unmatched
  generators (do not default-assign) — nothing is silently mis-paradigmed.
- Dry-run by default (prints the generator→paradigm table); `--commit` writes.
- Held-out GT/reference assets keep their true paradigm (a scan GT → `capture_scan`) but are
  already excluded from ranking via `is_gold`; the backfill does not change that.
- Idempotent: re-running produces the same assignment.

## C. Guardrail

**Pure predicate** in `app/paradigms.py`: `same_paradigm(gen_a, gen_b) -> bool`.

**Matchmaking** (`app/matchmaking.py`):

- `pick_pair(db, task, exclude_fn)` only returns a pair whose two outputs' generators share a
  paradigm. Implementation: group `_real_outputs(task)` by generator paradigm and draw the
  pair from within one paradigm group; if no paradigm group on the task has ≥2 outputs, return
  `None` (no valid same-paradigm pair) rather than crossing paradigms.

**Rating aggregation** (`app/service.py` + `app/ranking.py`):

- The Vote→Comparison stream that feeds `bradley_terry` is filtered to comparisons where both
  outputs' generators share a paradigm; cross-paradigm comparisons (including pre-existing
  ones) are dropped. A full rerun then yields clean within-paradigm ratings. Because
  generator→paradigm is 1:1, each generator's rating is inherently within its paradigm; no new
  rating-scope key is added.

## D. Minimal UI

- Leaderboard view: add a `paradigm` column and a paradigm filter (query param) that narrows
  the board to one paradigm. Default view groups/labels by paradigm so a scan and an LLM mesh
  are never shown in one ranked list.
- `/coverage`: add paradigm as a facet (counts per paradigm).
- Copy: label paradigms with human-readable names (map in `app/paradigms.py`).

## Testing

- `app/paradigms.py`: `is_valid_paradigm`, `same_paradigm` (same → True, differing → False,
  missing/empty paradigm → False, not a crash), display-name map covers every `PARADIGMS`
  entry.
- Backfill: a fixture set of generators covering all five families maps correctly; an unmapped
  generator raises with its name listed; dry-run writes nothing; `--commit` sets paradigms;
  idempotent on re-run.
- Matchmaking: `pick_pair` on a task with outputs from two paradigms never returns a
  cross-paradigm pair; returns a valid pair when a paradigm has ≥2 outputs; returns `None`
  when no paradigm has ≥2.
- Rating aggregation: a vote set containing a cross-paradigm comparison is excluded from the
  BT input; within-paradigm comparisons are retained; re-rank is deterministic.
- Existing suite stays green (no cross-paradigm regressions).
- Real-execution check: run the backfill dry-run against the study DB and inspect the
  generator→paradigm table before `--commit`.

## Risks

- **Unmapped generators** — mitigated by fail-loud backfill; the operator inspects the dry-run
  table first.
- **Tasks with only cross-paradigm outputs** become unpairable (pick_pair returns None) —
  acceptable and correct; surfaced in `/coverage` (a task with <2 same-paradigm outputs has no
  votable pair for that paradigm). Not hidden.
- **Existing ratings shift** when cross-paradigm votes are dropped — expected and desired; this
  is the confound being removed. Communicate in the leaderboard copy.
