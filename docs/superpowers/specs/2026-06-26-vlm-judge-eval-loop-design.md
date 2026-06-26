# VLM-Judge Eval Loop + Human Calibration Study — Design

> Status: approved (brainstorming) — 2026-06-26
> Next: `superpowers:writing-plans` → implementation plan.

## Goal

Populate the bio3d-arena leaderboard by adding a **VLM-as-judge** that votes on rendered
3D-model pairs, **and** measure how well that judge agrees with human votes — turning the
eval loop itself into a calibration result: _can a VLM be trusted to judge 3D plant quality,
and how much does seeing more of the model help?_

## Decisions (locked during brainstorming)

| Decision                                | Choice                                                                                                                             |
| --------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| Vote driver                             | **Both** — VLM judge fills the grid; VLM↔human agreement is a measured deliverable.                                                |
| Human budget                            | **You alone, ~150 votes**; voting UI stays shareable for later voters.                                                             |
| Perception ladder (experimental factor) | `single` (1 view) → `multi4` (4-view contact sheet, **main**) → `turntable` (8 views, subtest).                                    |
| Criteria                                | **3 contrasting:** `overall`, `visual_quality`, `structural_accuracy` (~50 calibration pairs each).                                |
| Architecture                            | **Approach A** — separate judge tables; reuse existing Bradley-Terry math; human loop untouched.                                   |
| Judge model                             | **Claude Sonnet 4.6** for the bulk judge; `judge_model` recorded per vote so an Opus-4.8 ceiling run on the subset is a later add. |
| Grid-fill scope                         | Full grid under **`multi4` × 3 criteria**; perception ladder (all 3 conditions) only on the ~150-pair calibration subset.          |

## Context (verified against live source, 2026-06-26)

- **Vote schema** (`app/models.py:141`): human `Vote` keyed by `session_id`, `winner ∈ {a,b,tie,bad}`;
  trust in `VoterSession` gates the Bradley-Terry leaderboard (trust ≥ 0.5). No `voter_type` column.
- **Pairing** (`app/matchmaking.py:41`): least-compared-first, randomized A/B, 10% gold attention checks.
- **Ranking** (`app/ranking.py`): online Elo on write (`elo_update`, K=32) + batch Bradley-Terry
  (`bradley_terry`, MM fit, 200-bootstrap 95% CI) recomputed via `/admin/recompute`
  (`app/service.py`). `bradley_terry(players, matches, …)` takes `matches` as a list of
  `(winner_id, loser_id)` — **reusable as-is for VLM votes**.
- **Renders** (`scripts/render_spotlight.py`): Playwright + `<model-viewer>` → 512×512 PNG on
  neutral-gray bg, cached in `Critique.render_path`. Only pre-baked for spotlight subjects.
- **Schema migration style:** `create_all`-only; the codebase **deliberately avoids `ALTER`s**
  (`app/models.py:288,336`, `ReconTask` pattern). → New work uses **new tables only**, never new
  columns on existing tables.
- **Deps:** Pillow ≥10, numpy, scipy already present. `anthropic` SDK **not** a dependency yet
  (add it). `ANTHROPIC_API_KEY` is set in the environment (verified, len 108).
- **Criteria seeded** (`app/seed.py:293`): `overall, realism, morphology, structural_accuracy,
visual_quality, scientific_usefulness`.
- **DB target:** the **worktree** `data/arena.db` holds the real coverage outputs; the main
  checkout DB is empty. The eval loop runs against the worktree DB. (`data/` is gitignored
  per-worktree runtime state.)

## Architecture

Three new tables (all `create_all`-friendly), one new render mode, two new scripts, one new
`/api/next` mode, and a dual-scope leaderboard. The human voting/integrity path is untouched.

```
render_spotlight.py (extended)  ──> contact sheets  data/assets/renders/{oid}_{cond}.png
build_calibration_set.py        ──> CalibrationPair rows (shared human+VLM subset)
judge_vlm.py  ──(Claude Sonnet 4.6 vision)──> JudgeVote rows ──recompute──> JudgeRating
/api/next?set=calibration       ──> human Vote rows on the same CalibrationPair pairs
calibration_report.py           ──> κ, rank-corr, self-consistency, ladder trend → results md
```

## Components

### 1. Data model — `app/models.py` (new tables only)

**`JudgeVote`** — one VLM judgment.

```python
class JudgeVote(Base):
    __tablename__ = "judge_vote"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("task.id"), index=True)
    output_a_id: Mapped[int] = mapped_column(ForeignKey("model_output.id"), index=True)  # real output shown in slot A
    output_b_id: Mapped[int] = mapped_column(ForeignKey("model_output.id"), index=True)  # real output shown in slot B
    criterion_id: Mapped[int] = mapped_column(ForeignKey("criterion.id"), index=True)
    winner: Mapped[str] = mapped_column(String(8))            # 'a' | 'b' | 'tie' | 'bad' (refers to slot)
    view_condition: Mapped[str] = mapped_column(String(16), index=True)  # single|multi4|turntable
    judge_model: Mapped[str] = mapped_column(String(48))      # e.g. 'claude-sonnet-4-6'
    swap_group: Mapped[str] = mapped_column(String(64), index=True)  # links the A/B & B/A orders
    rationale: Mapped[str] = mapped_column(Text, default="")
    created: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)
```

`output_a_id`/`output_b_id` are the **presented** slots — winner `'a'`/`'b'` maps back to a real
output through them, so the order-swap is captured without a redundant field. No DB-level
uniqueness; the harness dedups/resumes by `swap_group`.

**`JudgeRating`** — VLM-side cached ranking; mirrors `Rating` plus a `view_condition` key.

```python
class JudgeRating(Base):
    __tablename__ = "judge_rating"
    __table_args__ = (
        UniqueConstraint("generator_id", "category_id", "criterion_id", "view_condition",
                         name="uq_judge_rating_scope"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    generator_id: Mapped[int] = mapped_column(ForeignKey("generator.id"), index=True)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("category.id"), nullable=True, index=True)
    criterion_id: Mapped[int] = mapped_column(ForeignKey("criterion.id"), index=True)
    view_condition: Mapped[str] = mapped_column(String(16), index=True)
    elo: Mapped[float] = mapped_column(Float, default=1000.0)
    bt_score: Mapped[float] = mapped_column(Float, default=1000.0)
    bt_lower: Mapped[float] = mapped_column(Float, default=1000.0)
    bt_upper: Mapped[float] = mapped_column(Float, default=1000.0)
    n_games: Mapped[int] = mapped_column(Integer, default=0)
    judge_model: Mapped[str] = mapped_column(String(48), default="")
```

**`CalibrationPair`** — the shared subset humans and the VLM both vote.

```python
class CalibrationPair(Base):
    __tablename__ = "calibration_pair"
    __table_args__ = (
        UniqueConstraint("task_id", "output_a_id", "output_b_id", "criterion_id",
                         name="uq_calibration_pair"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("task.id"), index=True)
    output_a_id: Mapped[int] = mapped_column(ForeignKey("model_output.id"))
    output_b_id: Mapped[int] = mapped_column(ForeignKey("model_output.id"))
    criterion_id: Mapped[int] = mapped_column(ForeignKey("criterion.id"), index=True)
    created: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)
```

### 2. Render pipeline — extend `scripts/render_spotlight.py`

Reuse the existing Playwright + `<model-viewer>` capture (neutral-gray bg, 512×512). Add a
`render_contact_sheet(glb_path, condition)` that captures multiple azimuths and tiles them with
Pillow, caching by convention `data/assets/renders/{output_id}_{condition}.png` (idempotent —
skip if present):

- `single` — 1 view (azimuth 30°, elevation 75° — the existing spotlight angle). 512×512.
- `multi4` — azimuths 0/90/180/270 at elevation 70°, tiled **2×2** → 1024×1024.
- `turntable` — azimuths 0/45/…/315 (8) at elevation 70°, tiled **4×2** → 2048×1024.

Camera elevation/orbit are passed to the model-viewer page via query params; the existing
transient-HTTP-server + screenshot loop is reused per angle.

### 3. VLM judge harness — `scripts/judge_vlm.py`

For each work item `(task, out_a, out_b, criterion, view_condition)`:

1. Ensure both contact sheets exist (call the renderer; render on demand).
2. Build a prompt: species + task prompt, the criterion's rubric text (from `seed.CRITERIA`),
   and the two contact sheets labeled **Model A** / **Model B**.
3. Call **Claude Sonnet 4.6** vision (Anthropic SDK) with a **structured tool/JSON** response:
   `winner ∈ {a,b,tie,bad}` + one-sentence `rationale`. Parse defensively (malformed → record
   as `bad`/skip with a logged warning; never silently drop).
4. Write a `JudgeVote`.

**Position-bias control:** each logical comparison runs **both orders** (A/B and B/A) under one
`swap_group`. An order-dependent flip is recorded — the judge's analogue of the human gold
checks — and yields a self-consistency / position-bias metric per condition.

**Batch driver:**

- **Full grid** under `multi4` × 3 criteria (the leaderboard fill).
- **Calibration subset** under all 3 conditions × 3 criteria (the perception ladder).
- **Resumable:** skip `swap_group`s already present. **Cost cap:** `--max N` votes.
- **Run via jobd** (`job submit --project bio3d-arena --cwd $(pwd) --wait -- …`), babysat — it is
  a long, many-call batch. Network-bound (no `--gpu`).

`judge_model` is stored per vote; an Opus-4.8 ceiling run on the subset is a later `--model`
invocation, not a rework.

### 4. Calibration subset + analysis

**Sampler — `scripts/build_calibration_set.py`:** stratified ~150 pairs across the 6 species ×
the 3 criteria (~50/criterion), biased toward least-compared pairs and broad generator coverage;
idempotent (clears + rebuilds, or `--append`). Persists `CalibrationPair` rows.

**Report — `scripts/calibration_report.py`**, computed on the shared subset:

- **Cohen's κ** (human vs VLM winner) per `(criterion × view_condition)`. κ hand-coded
  (contingency table over `{a,b,tie}`; `bad` votes excluded pairwise).
- **Spearman + Kendall** rank correlation between the human-only `Rating` leaderboard and the
  VLM-only `JudgeRating` (`multi4`) leaderboard, per criterion. Via `scipy.stats`.
- **Judge self-consistency:** order-swap flip rate per condition (position bias).
- **Perception-ladder trend:** κ as a function of `single → multi4 → turntable` — does more
  viewpoint coverage move the VLM toward the human's full-orbit access?
- Output: `docs/results/2026-06-26-vlm-calibration.md` + a small `/benchmark` panel.

### 5. Human-voting path — calibration mode

Add `set` param to `/api/next` (`app/main.py`): when `set=calibration`, serve only
`CalibrationPair` pairs the current session hasn't voted, cycling the 3 criteria, with a
`progress` field (`{"voted": 47, "total": 150}`). Reuses `Comparison`/`Vote`/`apply_vote`/trust
unchanged. A banner/link on `/` enters calibration mode. When the set is exhausted, return a
"calibration complete" payload.

### 6. Leaderboard recompute (dual scope)

Extend the recompute (`app/service.py`) with a VLM pass: for each `(criterion, view_condition=multi4)`,
build `matches` from `JudgeVote` (expand `winner` to `(winner_id, loser_id)`, drop `tie`/`bad`
per the existing human convention), call the **existing** `ranking.bradley_terry(...)`, and upsert
`JudgeRating`. `/leaderboard` renders human vs VLM side-by-side (toggle/column). Human
`Rating` recompute path is unchanged.

## Data flow

1. Outputs already exist in the worktree DB. `build_calibration_set.py` selects the shared subset.
2. `judge_vlm.py` (jobd) renders contact sheets + writes `JudgeVote` (grid `multi4`; subset all
   conditions), both orders.
3. You vote the calibration subset via `/api/next?set=calibration` → human `Vote` rows.
4. Recompute → `JudgeRating` (VLM leaderboard) beside human `Rating`.
5. `calibration_report.py` → κ / rank-corr / self-consistency / ladder trend → results md + panel.

## Error handling

- **Render failure** (Playwright/model-viewer): record the output as un-renderable, skip its
  comparisons, log; never write a `JudgeVote` from a missing sheet.
- **VLM malformed/empty response:** retry once; on second failure log + skip the `swap_group`
  (do not fabricate a winner). Surface counts in the batch summary.
- **API/rate errors:** exponential backoff; `--max` cap bounds spend; resumable so a killed batch
  continues. Wall-clock guard on any inline one-shot per repo policy.
- **Empty/own-pair guards:** sampler and harness skip self-pairs and tasks with <2 outputs
  (matches `matchmaking` invariants).

## Testing

**Unit (API mocked):**

- Contact-sheet compositor: N stub images → correct tile grid + canvas dimensions per condition.
- Prompt builder: criterion rubric injected, Model A/B labels correct, both sheets attached.
- Winner parser: handles `a/b/tie/bad` + malformed/empty (→ skip, not crash).
- κ on a known contingency table → known value; Spearman on a known ranking.
- Sampler stratification: per-criterion/species counts within tolerance; no self-pairs; idempotent.
- `JudgeVote` resume/dedup by `swap_group`.
- Recompute: `JudgeVote` fixtures → expected `JudgeRating` ordering (reuses `bradley_terry`).

**Real-execution checks (per project doctrine):**

- Render **one real output** to a `multi4` sheet; assert the file exists and is non-empty.
- **One live Claude vision call** on a real rendered pair (gated on `ANTHROPIC_API_KEY`;
  `pytest.skip` when absent) — verifies the SDK call, image encoding, and parse end-to-end.

## Dependencies & ops

- Add `anthropic` to `requirements.txt`. `ANTHROPIC_API_KEY` already set.
- Pillow / numpy / scipy already present.
- Judge batch runs via **jobd** (`--project bio3d-arena`, no `--gpu`), babysat with a Monitor.
- Runs against the **worktree** `data/arena.db`. Confirm output counts before the batch.

## Out of scope (YAGNI)

- Opus-4.8 ceiling run (enabled later via `--model`, no rework).
- Additional human voters / `human↔human` ceiling (UI already supports it; not built now).
- The other 3 criteria (`realism`, `morphology`, `scientific_usefulness`).
- Multi-view for the human UI (humans use the live 3D viewer).
- #21 multi-view recon and #25 difficulty tiers (deferred — need imaging).
