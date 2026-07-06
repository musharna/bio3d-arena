<!-- ROOT_CAUSE_OK: design spec, not a bug fix -->

# K-wise (K=4) Voting — Design Spec

**Date:** 2026-07-04
**Status:** approved (brainstorming), pre-plan
**Goal:** Add a simultaneous 4-up "pick the best" ballot to the arena so one voter action yields 3 pairwise relations instead of 1, attacking the cold-start / low-vote-volume problem at launch — **without** touching the ranking engine.

## Why

The single biggest competitive gap for bio3d is vote **volume** (3D Arena has 123k votes; bio3d launches with 0). K-Sort Arena (arXiv 2408.14468) shows K-wise comparison converges to a correct ranking far faster than pairwise Elo because one simultaneous judgment over K models carries ~K−1 pairwise relations. We capture that signal-per-action gain while reusing bio3d's existing Bradley-Terry/Elo/CI/trust machinery.

## Grounding / methodology (do not re-derive)

- **Rank-breaking** (Azari Soufiani et al.): a K-way outcome decomposes into pairwise `(winner, loser)` records that feed a pairwise Bradley-Terry pipeline. Field-standard reduction.
- **Top-1 breaking** (pick-best): the winner beats each of the other K−1; the losers are left **unordered** (no invented ranking). This is the _statistically consistent, low-variance_ breaking — the reason we choose pick-best over full-rank for v1.
- **Non-independence caveat:** the K−1 pairs from one ballot share one voter judgment and are NOT independent observations. Naïvely counting each as an independent vote **understates uncertainty** (over-tight CIs). Mitigation: compute BT confidence intervals with a **ballot-level bootstrap** (resample whole ballots, not individual pairs).
- The K-Sort "16.3× faster" figure is a _synthetic_ 50-model convergence simulation, not a human-agreement result. The honest, claimable benefit is **3× more pairwise signal per user action**.

## Global constraints

- **Reuse the existing pipeline.** No new ranking model. Derived pairs flow through `service.apply_vote` → Elo + `ranking.bradley_terry` exactly like native pairwise votes.
- **Bradley-Terry is computed WITHIN each paradigm group** (confirmed in `app/main.py::_leaderboard_rows` / `_judge_leaderboard_rows`). Therefore the 4 shown outputs MUST be 4 distinct generators' outputs for **one task, one paradigm**. Cross-paradigm 4-ups are invalid.
- **K-wise is additive.** Pairwise remains for gold checks, calibration, and any task lacking ≥4 same-paradigm outputs.
- **Gold/attention checks stay pairwise-only in v1.** K-wise ballots inherit trust filtering via the voting session's existing gold history.
- Test runner: `.venv/bin/pytest`. NEVER `BIO3D_DATABASE_URL=study`.
- K fixed at 4.

## Architecture

A **collection-and-presentation layer** on top of the unchanged ranking engine. Three moving parts: a matchmaker that assembles a 4-up, an endpoint that records the pick and **expands it into 3 pairwise votes**, and a one-line change to the bootstrap so CIs stay honest.

### 1. Data model

New table `KBallot` (audit trail of what was shown + the pick):

| column          | type                      | notes                                |
| --------------- | ------------------------- | ------------------------------------ |
| id              | int PK                    |                                      |
| task_id         | FK task                   |                                      |
| criterion_id    | FK criterion              |                                      |
| session_id      | str(64) index             |                                      |
| output_ids_json | Text                      | the 4 output IDs shown, JSON list    |
| best_output_id  | FK model_output, nullable | NULL = "can't tell / all bad" (skip) |
| created         | datetime                  |                                      |

Add **`ballot_id`** (nullable FK → `kballot.id`, indexed) to `Comparison`. Native pairwise comparisons leave it NULL. The 3 derived comparisons of one K-ballot share its `ballot_id`.

_No change to `Vote`, `Rating`, or the BT tables._ `Comparison` already carries the schema-drift guard pattern; add `KBallot` to `_FORCE_DELETE_MODELS` (per the recurring create_all drift class — see `bad_output_handling_2026-07-03` memory).

### 2. Matchmaking

`matchmaking.pick_quad(db, task, criterion, session) -> list[ModelOutput] | None`:

- Reuse `_paradigm_groups`; find a paradigm group on the task with **≥4 admitted real outputs** (the admissibility gate — `non_admitted_output_ids` — must be excluded from the quad exactly as it is from `pick_pair`'s pool, so the semantic/structural/completeness gate keeps gated junk out of the 4-up) that the session hasn't already seen as a K-ballot for this criterion.
- Return 4 (freshness-biased, mirroring `pick_pair`'s existing selection). `None` if no group qualifies → caller falls back to `pick_pair`.
- v1 uses the current freshness/pairing heuristics extended to 4; **explore-exploit UCB (K-Sort's σ-driven scheduler) is explicitly out of scope for v1.**

### 3. Endpoints

- `GET /api/next?mode=kwise&criterion=&category=` → `_build_kwise_comparison`: pick a task (existing `pick_task`) + `pick_quad`. If a quad is available, return `{ballot_id, task, criterion, outputs:[4]}`. **If not, return a normal pairwise payload** (transparent fallback — the client renders whichever shape it gets).
- `POST /api/kvote {ballot_id, best_output_id | null}`:
  1. captcha + rate-limit (reuse `integrity`).
  2. Load `KBallot`; reject if already resolved (has derived comparisons) or unknown.
  3. If `best_output_id` is NULL ("all bad") → record the skip, produce **no** relations, advance.
  4. Else validate `best_output_id ∈ output_ids`. For each of the other 3 outputs `L`: create `Comparison(output_a=best, output_b=L, criterion, session, ballot_id=KBallot.id)` + `Vote(winner='a', session)`, then `service.apply_vote`.
  5. `note_vote` once per derived vote (rate accounting) — OR once per ballot; **decision: once per ballot** (a ballot is one user action; charging 3 against the rate limit would throttle honest voters). Record in plan.
  6. Commit; return `{status, next}` (next K-wise or pairwise).

### 4. Ranking + CIs (the only engine touch)

`ranking._bootstrap_scores` currently resamples the flat match list. Change it to **resample by ballot group**: group derived matches by `ballot_id`, treat each native pairwise comparison as its own singleton group, resample groups with replacement, then flatten. Point estimates (BT MLE, Elo) are unchanged; only the CI widths change (they widen to honest values). Add a regression test asserting CIs from ballot-grouped resampling are ≥ CIs from naïve per-pair resampling on the same data.

### 5. Trust, dedup, gold

- **Trust:** derived votes carry `session_id`; the existing sub-`TRUST_THRESHOLD` exclusion applies unchanged.
- **Dedup:** at the **ballot level** — a session may not be served the same 4-set (same task+criterion) twice (`pick_quad` excludes seen quads). Derived pairwise votes will not collide with native pairwise dedup because they are new `Comparison` rows tied to the ballot.
- **Gold:** none injected into K-wise in v1.

## Client (arena UI)

The arena page renders a **4-up grid** (4 model-viewers) with a "Pick the best" affordance and a "Can't tell / all bad" button when the payload has 4 outputs; it renders the existing 2-up when the payload is a pairwise fallback. Reuse the existing viewer component and vote-POST plumbing (`arena.js`), branching on payload shape. No new viewer tech.

## Error handling

- Unknown/al­ready-resolved ballot → 404/409 (mirror `api_vote`).
- `best_output_id` not among the 4 → 400.
- Fail-loud per the codebase norm; no silent fallbacks.

## Testing

1. Decomposition: pick-best over `[a,b,c,d]` with best=`a` → exactly `(a>b),(a>c),(a>d)`, all stamped one `ballot_id`; losers never compared to each other.
2. "All bad" (best=NULL) → KBallot recorded, **zero** Comparison/Vote rows, advances.
3. Ballot-level bootstrap: on a fixture with K-ballots, ballot-grouped CIs are **wider** than naïve per-pair CIs (uncertainty not fake-tightened).
4. Matchmaking: task with ≥4 same-paradigm outputs → quad served; task with 3 → pairwise fallback; cross-paradigm never mixed into one quad.
5. Trust filter still excludes sub-threshold sessions' derived votes from the authoritative board.
6. Rate limit charged once per ballot, not per derived vote.
7. Schema-drift guard: `KBallot` in `_FORCE_DELETE_MODELS`; `test_seed_force_cascade` stays green.

## Out of scope (v1)

Explore-exploit UCB matchmaking; K-wise gold checks; best-worst (pick best AND worst); full Rank mode; any change to Elo/BT point estimates.

## Success criteria

One K-wise "pick best" click records 3 correct within-paradigm pairwise relations that move the existing BT/Elo leaderboard, with confidence intervals computed by ballot-level bootstrap; pairwise arena unaffected; full suite green.
