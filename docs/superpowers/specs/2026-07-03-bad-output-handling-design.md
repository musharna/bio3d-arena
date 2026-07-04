# Bad-output handling — completeness auto-gate + human flag (design)

**Status:** approved design, 2026-07-03. Feeds `writing-plans`.

## Problem

The arena serves clearly-failed / not-a-plant outputs into the vote pool (observed live: a
tomato-reconstruction pair where Model B was a degenerate grey triangle). The current vote
options can't express "A is fine, B is garbage" cleanly (`B is better` is wrong, `Both bad`
is wrong; `A is better` is the correct vote but the human's attention was still wasted on
obvious garbage, and B keeps occupying pairings). This is a **pool-hygiene** problem, not a
vote-outcome problem.

Two complementary layers handle it:

1. **Auto-gate** — reuse the already-built D-Complete completeness scorer to keep sub-threshold
   outputs (not a whole plant) out of the vote pool automatically. Scales, no human effort.
2. **Human flag** — a per-model "not a plant / failed" button as the safety net for what the
   auto-gate misses, which also generates human-labeled failure data.

## Grounded current state (verified 2026-07-03)

- `ModelOutput` has **no** `hidden`/`flagged`/`active` column (cols: id, task_id, generator_id,
  title, asset_path, asset_format, meta_json, n_comparisons, is_gold, created, source, license,
  attribution, external_url).
- The vote pool gates **only** on `is_reference_scan(o.source) or is_untextured_output(o)`
  (`app/main.py` `_build_comparison._vote_excluded`), applied with `exclude_fn` parity across
  `matchmaking.pick_task` + `pick_pair`.
- **D-Complete exists** — `Completeness` table (output_id, `category`, `score` 0–1, checklist_json,
  judge_model, scorer_version, computed) + `/api/completeness.json` + `service.completeness_rows`.
  It is a metric/board only, **not** a pool gate. Categories in the study copy:
  `complete=142, isolated-organ=47, fragment=24, partial-organism=6`; 219/306 outputs scored,
  87 unscored. Score is ~binary (complete=1.0; others 0.0–0.6).
- The moderation surface (`/admin/moderation`, `moderation_page`) handles community **submissions**
  only, not outputs already in the pool.
- Vote winners are `a | b | tie | bad`; the `bad` ("Both bad") vote is a symmetric outcome, NOT a
  per-model report — orthogonal to this feature.

## Component 1 — completeness auto-gate

Extend the vote-pool exclusion so `_build_comparison` also drops outputs whose completeness
`category` is in the **failure set** `{isolated-organ, fragment}`. Keep `complete`,
`partial-organism` (still a plant, just incomplete → a fair comparison), and **unscored outputs**
(conservative: no mass-disappearance; scores backfill over time).

- **Config:** `POOL_EXCLUDED_COMPLETENESS_CATEGORIES` = `{"isolated-organ", "fragment"}` (a set
  constant in `app/config.py`, tunable). An empty set disables the gate.
- **Mechanism:** in `_build_comparison`, precompute the set of excluded output-ids ONCE (a single
  query joining `ModelOutput`→latest `Completeness`, selecting ids whose category ∈ failure set),
  and fold that membership test into the existing `_vote_excluded(o)` closure. This keeps the
  per-output `exclude_fn` O(1) and preserves pick_task/pick_pair parity (the lesson from the prior
  audit: the SAME predicate must gate task and pair selection, else intermittent 404s).
- **Read-only** w.r.t. D-Complete: reads `Completeness.category`; no change to the scorer
  (another agent's territory).
- **Consistency:** excluded from the _vote_ pool only; the outputs remain in the Mode-B/benchmark
  and completeness boards, exactly as untextured/reference outputs already do.
- **"latest" completeness:** an output may in principle have multiple `Completeness` rows
  (re-scores); use the most recent by `computed` per output_id.

## Component 2 — per-model human flag

### Schema

- **New table `OutputFlag`:** `id` (pk), `output_id` (FK→model_output), `session_id` (str),
  `reason` (str enum: `not_a_plant | failed | other`), `created` (datetime). Distinct-session
  counting is over `(output_id, session_id)`.
- **New column `ModelOutput.hidden_at`** (nullable datetime). Non-null ⇒ excluded from the vote
  pool (added to `_vote_excluded`). Set by auto-hide (K threshold) or admin Confirm-hide; cleared
  by admin Restore.

### Endpoint `POST /api/flag`

Body `{output_id: int, reason: str}`. Steps:

1. Rate-limit by session (reuse `integrity.check_rate_limit`) → 429 on abuse.
2. Validate `output_id` exists (404) and `reason` ∈ allowed set (422).
3. Upsert-dedup: if this `(output_id, session_id)` already flagged, return ok idempotently (no
   double-count). Else insert an `OutputFlag`.
4. Recount distinct sessions that have flagged this output. If
   `count >= BIO3D_FLAG_HIDE_THRESHOLD` (config `FLAG_HIDE_THRESHOLD`, default **3**) and
   `hidden_at` is null → set `hidden_at = now()` (auto-hide pending admin review).
5. Return `{status: "ok", hidden: bool, flags: count}`.

- **Config:** `FLAG_HIDE_THRESHOLD` = int env `BIO3D_FLAG_HIDE_THRESHOLD` (default 3). On the audit
  instance it can be set to 1 so a single flag hides immediately.

### UI

- A small **⚑** button added to each viewer's control bar (`app/static/viewer.js` `addControls`,
  beside ⟳ reset and ⛶ fullscreen), `aria-label`/`title` = "Flag: not a plant / failed".
- Click → lightweight confirm → `POST /api/flag` with the slot's `output_id` and
  `reason="not_a_plant"` → on ok the button shows a `flagged ✓` state (disabled) and a status line
  message. Failure surfaces the reason (never a silent success — mirror the arena.js vote() fix).
- **The arena must expose each slot's `output_id` to the client.** `/api/next` currently anonymizes
  the payload (no generator identity). `output_id` is an opaque asset id (not a generator/model
  identity), so adding `a.output_id` / `b.output_id` to the served payload does not de-anonymize the
  comparison. `arena.js` stashes them on the slots for the flag button.
- Flagging is **decoupled from voting** — it does not cast a vote or advance the pair. The user may
  still vote or move on.

### Admin

- Extend `/admin/moderation` (or a sibling admin view) with a **flagged/hidden outputs** section:
  each row shows output id + thumbnail + task + distinct-flag count + reasons, with **Restore**
  (clear `hidden_at`) and **Confirm-hide** (set `hidden_at` if not already) actions, token-gated
  like the existing moderation actions.

### Failure-label payoff

`OutputFlag` rows + the `hidden_at` set form a human-labeled _failure_ dataset (per output, with
reason). It is exportable and feeds D-Complete calibration (agreement vs the VLM category) and
D-Gen (hard negatives). No new export UI in this feature — the rows are queryable.

## Data flow

```
/api/next pool = outputs − ( is_reference_scan ∪ is_untextured_output
                             ∪ completeness.category ∈ {isolated-organ, fragment}
                             ∪ hidden_at IS NOT NULL )
   (same exclude_fn across pick_task + pick_pair; voted-pair exclusion unchanged)

flag: user clicks ⚑ → POST /api/flag → OutputFlag insert (dedup per session)
      → if distinct-session count ≥ K → hidden_at = now  → out of pool on next /api/next
admin: Restore clears hidden_at (back in pool); Confirm-hide sets it (out of pool)
```

## Components / files

- `app/config.py` — `POOL_EXCLUDED_COMPLETENESS_CATEGORIES`, `FLAG_HIDE_THRESHOLD`.
- `app/models.py` — `OutputFlag` model; `ModelOutput.hidden_at`.
- `app/service.py` (or a small `app/flags.py`) — `excluded_output_ids_by_completeness(db)`,
  `record_flag(db, output_id, session_id, reason) -> (hidden, count)`, `distinct_flag_count(...)`.
- `app/main.py` — extend `_build_comparison._vote_excluded` (completeness set + `hidden_at`);
  `POST /api/flag`; admin flagged-outputs section + Restore/Confirm-hide routes; add `output_id`
  to the `/api/next` serialized payload.
- `app/static/viewer.js` — ⚑ control in `addControls` (accept an `onFlag` hook / output id).
- `app/static/arena.js` — stash slot output ids; wire the ⚑ button to `/api/flag`.
- `app/templates/moderation.html` (+ arena.html if the button needs a hook) — admin rows.

## Testing

- **Auto-gate:** an output categorized `isolated-organ`/`fragment` is never returned by
  `_build_comparison`/pick_pair; `complete`/`partial-organism`/**unscored** still eligible.
  Parity: pick_task does not offer a task whose only pair is gate-excluded (no 404 regression).
- **Flag endpoint:** records a flag; a second flag from the SAME session does not double-count;
  reaching K distinct sessions sets `hidden_at`; below K leaves it null; rate-limit → 429;
  unknown output → 404; bad reason → 422.
- **Hidden exclusion:** an output with `hidden_at` set is never served by pick_pair/pick_task.
- **Admin:** Restore clears `hidden_at` (re-enters pool); Confirm-hide sets it.
- **Payload:** `/api/next` includes `a.output_id`/`b.output_id` and still leaks no generator identity.
- **Client:** the ⚑ button posts the correct output_id + reason and reflects failure (not silent ok).

## Global constraints

- Test runner `.venv/bin/pytest`; **never** `BIO3D_DATABASE_URL=study`. Baseline 642 passed / 8 skipped.
- New `ModelOutput.hidden_at` column + `OutputFlag` table are created by `create_all` on a fresh DB;
  the running study/public DBs get them via the deploy re-import (fresh schema), consistent with the
  existing schema-drift handling — direct-serving an old DB needs the column added (as with
  `voter_session.user_id`).
- Reuse verbatim: `integrity.check_rate_limit`, the `exclude_fn` seam + parity, the
  `require_admin_*` gate for admin actions, the viewer control-bar pattern, the arena.js
  non-silent-failure vote pattern. Do NOT edit the D-Complete scorer — read `Completeness.category`
  only.
- Flag button is decoupled from the vote; no public flag-count display (anti-brigading).

## Non-goals (YAGNI)

- No ML auto-flag beyond reusing the completeness category.
- No public flag-count / "reported N times" display.
- No new export UI for the failure labels (rows are queryable).
- No change to the vote winner set (`a|b|tie|bad` unchanged).
- No re-scoring / backfill of the 87 unscored outputs in this feature (they stay in the pool).
