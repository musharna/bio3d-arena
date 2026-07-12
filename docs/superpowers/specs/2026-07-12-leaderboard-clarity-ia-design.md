# Leaderboard Clarity IA — Design Spec

> Spec 1 of a two-spec initiative. Spec 2 (entrant diversity + effort-variants) is a
> follow-on that slots into the grouping seam this spec reserves.

**Goal:** Restructure the leaderboard so a visitor immediately understands _what each
ranking is a ranking of_ — making the generation modalities clearly delineated, the
evidence source unambiguous, and each rank feel earned — without changing how ranking is
computed.

**Non-goal:** generating new entrants/effort-variants (Spec 2), share cards (#75), a
`/difficulty` redesign, or the public deploy. Ranking math is unchanged.

## Background — the current problem

`/leaderboard` crams five orthogonal axes onto one surface:

1. **Modality/paradigm** — `image_recon`, `capture_scan`, `procedural_llm`, `text_native`,
   `agentic` (Overall tab + per-paradigm tabs). `retrieval` + `procedural_expert` are
   app-hidden (`config.APP_HIDDEN_PARADIGMS`) and stay hidden.
2. **Kingdom** — plants / fungi / animals (global scope pill).
3. **Criterion** — overall, botanical_plausibility, … (dropdown).
4. **Evidence source** — human votes vs the VLM-judge board (`/leaderboard/judge`,
   currently a buried collapsed panel).
5. **Vote trust** — all vs verified-only; provisional vs firm.

A newcomer cannot tell what a given board _is_. The fix is a clear hierarchy: **modality is
the spine**; everything else is a clearly-labeled secondary filter or a separate surface.

## Decisions (settled in brainstorming)

- **Primary spine = modality/paradigm.** The statistically rigorous unit — ranking is always
  within a single paradigm (`app/paradigms.py`: "Ranking is ALWAYS within a single paradigm
  value"; cross-paradigm pools are disconnected).
- **Landing = modality hub.** Cards for each visible modality, each drilling into a full board.
- **Evidence source: human-vote board is THE board.** The AI-judge board lives on its own
  clearly-labeled page, linked from each board — never intermixed.
- **Cross-paradigm "Overall" ranking is removed** from the primary flow, replaced by a
  one-line explainer (it is explicitly "not a statistical claim").
- **Kingdom stays the existing global filter.** **Criterion** is a secondary in-board filter.
- **Visible modality boards = 4:** Image→3D reconstruction, Text→3D, LLM Procedural (code),
  Agentic. **`capture_scan` is moved to internal/app-hidden** as part of this work — add it to
  `config.APP_HIDDEN_PARADIGMS` beside `retrieval` + `procedural_expert`. (It is photogrammetry
  of a live organism — a data-capture reference more than a competing generator, and thin;
  kept in the DB for internal analysis, hidden from the public boards.)

## Global Constraints

- **Within-paradigm ranking invariant:** every board ranks exactly one paradigm value. No
  surface may present a cross-paradigm BT score as a comparable number.
- **App-hidden paradigms never appear:** honor `config.APP_HIDDEN_PARADIGMS` +
  `service.mode_a_excluded_generator_ids` everywhere a board or card is built.
- **Modality display names** come from `paradigms.DISPLAY_NAMES` / `SHORT_NAMES` — do not
  hard-code new strings; add a `WHAT_THIS_MEASURES` map beside them for the one-liners.
- **Honest sparse state:** a board/card backed by `< FIRM_VOTE_THRESHOLD` votes reads as
  _evaluation-in-progress_ (a votes-until-firm signal), never as broken or as a settled rank.
- **No new ranking math:** reuse `service._matches_for_scope`, the existing per-paradigm/
  kingdom row builders, and `FIRM_VOTE_THRESHOLD`. This spec is presentation + one read-only
  aggregation (the head-to-head matrix).

## Surfaces

### A. Modality hub — `/leaderboard`

Landing shows one card per visible modality, under the global kingdom filter.

```
Leaderboard        [ Plants ▾ ]     ⓘ Each method is ranked on its own — scores aren't
                                       comparable across methods (separate match pools).
┌ Image→3D reconstruction ┐ ┌ Text→3D ┐ ┌ LLM Procedural (code) ┐ ┌ Agentic ┐
│ what: photo → 3D mesh    │ │ …       │ │ …                     │ │ …       │
│ 1 TRELLIS      1402      │
│ 2 Hunyuan3D    1361      │
│ 3 Meshy 6      1288      │
│ 16 models · firm         │   ← population + confidence state
│ view board →             │
└──────────────────────────┘
```

Card contents: modality name (`DISPLAY_NAMES`) · one-line `WHAT_THIS_MEASURES` · top-3 rows
(rank, name, score) for the current kingdom · population line (`N models · firm|provisional`)
· "view board →". The explainer line replaces the removed Overall ranking. A modality with no
rated entrants in the current kingdom shows an honest empty/provisional card, still clickable.

### B. Modality board — `/leaderboard/<modality>`

Full within-paradigm human-vote board for the selected modality + kingdom.

```
← Leaderboard   Image→3D reconstruction · Plants          [ criterion ▾ ]
   what this measures: a single photo reconstructed into a 3D mesh.
   Ranked by human votes · 1,204 cast · → see the AI-judge board

rank  model            score   95% CI    games   status
 1    TRELLIS          1402    ±48       312     firm
 …
 9    SAM 3D           1044    ±140      18      28 more votes → firm
```

- Rows reuse the existing per-paradigm row builder scoped to one paradigm + kingdom + criterion.
- `status` column carries the votes-until-firm signal (**#76**): `firm` when
  `games ≥ FIRM_VOTE_THRESHOLD`, else `"{FIRM_VOTE_THRESHOLD - games} more votes → firm"`.
- The AI-judge equivalent is a labeled link to `/leaderboard/judge?modality=…`, not inlined.
- Show-all / verified toggles are retained but visually subordinate.

### C. Model detail — `/models/<slug>`

Adds the **head-to-head win matrix (#74)**: for the model's own modality, its decisive record
vs each opponent — "beats Hunyuan3D 63% (n=41)" — computed read-only from
`service._matches_for_scope` filtered to the two generators. Also surfaces provenance chips
(paradigm, provider, license) already available on the model. A model with no decisive games
shows "not enough head-to-head data yet."

### D. AI-judge delineation — `/leaderboard/judge`

Unchanged in computation; gains a clear page title, a one-line "these ranks come from a VLM
judge, not human votes," and a modality selector mirroring the human boards so the two are
navigable in parallel rather than one being buried.

## Variant-aware hook (reserved for Spec 2)

Board rows key on a **model identity** that Spec 2 can expand into a parent-with-variants
group (e.g. `Hunyuan3D ▸ turbo / standard`). Spec 1 requirement: the row-rendering component
must accept an optional `variant_of` / child-rows grouping without structural change — a
data-shape seam only; no variant UI is built here.

## Data / backend

- **Hub:** for each visible paradigm, a top-N slice of the existing per-paradigm/kingdom row
  builder + a population/confidence summary (count of rated entrants; firm iff any row
  `games ≥ FIRM_VOTE_THRESHOLD`). One cached read per card; reuse `cached_kingdom_leaderboard_rows`.
- **Board:** existing per-paradigm rows scoped to (paradigm, kingdom, criterion) + the
  votes-until-firm status derived from `games` vs `FIRM_VOTE_THRESHOLD`.
- **Head-to-head (#74):** `service._matches_for_scope` already returns decisive
  `(winner_gen, loser_gen)` pairs; aggregate into a per-opponent win/loss/games map for the
  target model within its paradigm scope. Pure read; no schema change.
- **Copy:** new `paradigms.WHAT_THIS_MEASURES: dict[str, str]` beside `DISPLAY_NAMES`.

No migrations. No change to vote recording, matchmaking, or BT computation.

## Testing

- `paradigms.WHAT_THIS_MEASURES` covers every non-hidden paradigm (unit).
- Hub route: renders exactly the 4 visible modalities; `capture_scan`, `retrieval`, and
  `procedural_expert` never appear as public boards; each card carries name + what-measures +
  population line; respects the kingdom filter (route test).
- Board route: ranks one paradigm only; votes-until-firm status string correct on both sides of
  the threshold; AI-judge link present; app-hidden generators absent (route test).
- Head-to-head aggregation: given seeded decisive votes, win% and n are correct; a model with no
  games yields the empty state (unit + route).
- No cross-paradigm combined score is emitted anywhere (regression test asserting the Overall
  ranking route/section is gone).
- New layouts stay responsive (kept in scope so #77 doesn't have to redo them).

## Scope

**In:** modality hub, per-modality board, model-detail head-to-head (#74), votes-until-firm
(#76), judge-page delineation, kingdom/criterion filter cleanup, removal of the cross-paradigm
Overall ranking, moving `capture_scan` into `config.APP_HIDDEN_PARADIGMS` (internal-only), the
variant grouping seam.

**Out:** entrant/effort-variant generation (Spec 2), share cards (#75), `/difficulty` redesign,
mobile pass beyond keeping new layouts responsive (#77), public deploy (#33).
