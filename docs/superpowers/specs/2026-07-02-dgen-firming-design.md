# D-Gen firming — independent cross-judge A/B + multi-model generality (design)

**Status:** approved design, 2026-07-02. Feeds `writing-plans`.

## Goal

Turn D-Gen from a caveated demo into a credible result by answering the two weaknesses its own results
doc named: (1) fidelity was scored by the **same** VLM (`claude-sonnet-4-6`) that generated the critique
— circular; (2) a **single** model (gemini). Two signals, both against a **copy** of the study DB:

- **Multi-model same-judge lift** — does the sonnet-judged fidelity lift _replicate across models_?
- **Independent cross-judge A/B** — does a **different-lab** VLM, blind, _prefer the refined output over the
  round-0 baseline_? This is the judge-independent claim that escapes the circularity.

Grounded OWNED-AT-SEAM vs FloraForge (2512.11925) per `next_directions_triage_2026-07-02`: rubric-driven,
multi-taxon, and now independently-validated — the differentiators FloraForge (Chamfer-to-scan, single
species) would have to rebuild our stack to match.

## Scope (locked)

- **Tested generators (D-Gen runs):** `google/gemini-3.1-pro-preview` (already run, reused) +
  `anthropic/claude-opus-4.8` + `x-ai/grok-4.3` (2 fresh runs). 3-model generality signal.
- **Independent A/B judge:** `openai/gpt-5.1` — different lab from the sonnet generation-judge AND disjoint
  from the tested generators (nothing judges its own family).
- **A/B mechanism:** blind pairwise, run in **both orders** (position-bias cancel). Refined-preferred only
  if refined wins _both_ orders; a flip = inconsistent/tie.
- **Everything runs against a DB copy** — never `data/study/arena-study.db` (coordinated with the other
  agent, who is seeding that DB concurrently).

**Non-goals (YAGNI):** no new DB table / API / board (multi-model lift lives in the existing
`DGenRun`/`DGenIteration` tables; the A/B output is a results doc + a JSON sidecar); no live UI; no gemini
re-run; Chamfer is a caveated _secondary_ signal only.

## Which (model, taxon) pairs actually get A/B'd

For each run × taxon, the best round determines the bucket — the A/B only tests where refinement _changed_
the output:

- **`best_round > 0` AND round-0 baseline is a valid mesh → A/B pair** (the real test: baseline vs refined).
- **round-0 was invalid_mesh (no baseline GLB) but best is valid → `repair`** — refined trivially wins; count
  separately, do NOT send to the VLM (nothing valid to compare).
- **`best_round == 0` → `no-refinement`** — refined _is_ the baseline; skip the A/B, count separately.

The independent preference rate is computed over the A/B-pair bucket; repairs and no-refinement are reported
alongside so the denominator is honest. (For the gemini run this means Rosa + Solanum get A/B'd, Zea mays +
Glycine max are no-refinement, Arabidopsis + Pinus are repairs.)

## Key mechanism: composite A|B image (reuse `vision_complete` as-is)

`app/agentic.vision_complete(post, model_id, prompt, image_png, *, api_key, ...) -> str` takes ONE image.
So the A/B **composites** the baseline sheet and the best sheet **side-by-side into a single PNG** with the
left half labeled "A" and the right "B", and asks the judge which side is the better/more-complete `<taxon>`
plant. Both orders = two composites with the halves swapped. This reuses `vision_complete` verbatim (no edit
to `app/agentic.py` — imported only) and makes position-bias control a simple swap.

## Data flow (per run × taxon, A/B bucket)

```
best GLB     = ModelOutput(is_best iteration's output_id).asset_path                  # promoted, persisted
baseline GLB = {ASSET_DIR}/dgen_baseline/{run_id}_{taxon_slug}.glb                     # round-0, persisted (below)
sheet_best   = tile(capture_multi(best_glb, multi4))                                   # reuse score_glb render
sheet_base   = tile(capture_multi(baseline_glb, multi4))
comp_AB      = hstack(label(sheet_base,"A"), label(sheet_best,"B"))                    # order 1: baseline=A
comp_BA      = hstack(label(sheet_best,"A"), label(sheet_base,"B"))                    # order 2: baseline=B
pick1        = judge_pair(vision_fn, comp_AB, taxon, common)   # order 1 (baseline=A); refined="B"
pick2        = judge_pair(vision_fn, comp_BA, taxon, common)   # order 2 (baseline=B); refined="A"
verdict      = verdict_both_orders(pick1, pick2)
               # "refined"  if pick1=="B" and pick2=="A"  (refined won both orders)
               # "baseline" if pick1=="A" and pick2=="B"  (baseline won both orders)
               # "inconsistent" otherwise (a flip = position bias or genuine tie)
```

## Components

### `app/dgen.py` — persist the round-0 baseline (small add)

In `refine_loop`, when round 0 runs and produces a valid GLB (`status=="ok"`), copy that GLB to
`{asset_dir}/dgen_baseline/{run_id}_{taxon_slug}.glb` (mkdir the dir). This is the only D-Gen change — a
baseline artifact for the A/B, NOT a vote-pool output. Fresh runs (opus, grok) get it automatically; the
existing gemini run is backfilled by copying its `dgen_tmp/1_{taxon}_r0.glb` files into `dgen_baseline/`
(a one-line ops step in the driver / a `--backfill-from-tmp` flag).

### `app/dgen_ab.py` — the cross-judge A/B (new; pure + injected `vision_fn`)

```python
def composite_ab(sheet_left: bytes, sheet_right: bytes) -> bytes:
    """Side-by-side PNG: left half labeled 'A', right half 'B' (PIL, like tile_contact_sheet)."""

def ab_prompt(taxon: str, common: str) -> str:
    """Blind instruction: 'Two rendered 3D models of a <common> (<taxon>), side by side (A left,
    B right). Which is a more complete and botanically accurate whole <common> plant? Answer with a
    tool/JSON giving only "A" or "B".' No mention of baseline/refined."""

def judge_pair(vision_fn, comp_png: bytes, taxon: str, common: str) -> str | None:
    """vision_fn(prompt, image_png)->str ; parse the answer to 'A'|'B' (None if unparseable)."""

def verdict_both_orders(pick1: str | None, pick2: str | None) -> str:
    """pick1 = judge's pick with baseline=A (refined="B"); pick2 = with baseline=B (refined="A").
    'refined' iff pick1=="B" and pick2=="A"; 'baseline' iff pick1=="A" and pick2=="B"; else 'inconsistent'."""

def aggregate(rows: list[dict]) -> dict:
    """Per-model + pooled: {n_ab, refined, baseline, inconsistent, refined_rate, repairs, no_refinement}."""
```

`vision_fn` is injected (`functools.partial(agentic.vision_complete, httpx.post, judge_model, api_key=...)`
in the driver; a fake in tests). Pure image/parse/verdict/aggregate logic, no network.

### `scripts/run_dgen_ab.py` — driver (new)

For each `DGenRun` in the copy DB (the 3 models): bucket each taxon (A/B / repair / no-refinement); for A/B
taxa render baseline+best sheets (via `browser_capture_multi_factory` + `tile_contact_sheet`), composite
both orders, call `judge_pair` twice via `vision_complete` (judge = `--judge-model`, default
`openai/gpt-5.1`), record the verdict; aggregate; write `docs/results/2026-07-02-dgen-firming-results.md`

- a JSON sidecar. `--backfill-from-tmp` copies gemini's `dgen_tmp` round-0 GLBs into `dgen_baseline/`.
  NEVER `BIO3D_DATABASE_URL=study`.

### Chamfer secondary (optional, caveated)

For A/B taxa that have a held-out GT scan, additionally report `Chamfer(baseline)` vs `Chamfer(best)` as a
geometry sanity signal — with the explicit caveat (per the SP4 finding + triage) that Chamfer is a weak,
non-commensurable proxy for the morphology axis the metric targets, so it is secondary evidence only, never
the headline.

## Deliverable

`docs/results/2026-07-02-dgen-firming-results.md`:

1. **Multi-model same-judge lift table** — per model (gemini/opus/grok), the round-0→best fidelity lift per
   taxon + aggregate (from `service.dgen_trajectory` over the 3 runs). Does the effect replicate?
2. **Independent cross-judge A/B** — pooled + per-model: refined-preferred X/N, baseline-preferred, inconsistent;
   plus the repair and no-refinement counts. The headline: _does a different-lab judge, blind, prefer the
   refined output?_
3. Honest interpretation + the standing validity caveats (small n; the A/B tests only where refinement changed
   the output; Chamfer secondary).

## Testing

Offline unit tests (fake `vision_fn`, tiny real PNGs so PIL can composite):

- `test_composite_ab` — output is a valid PNG wider than either input (side-by-side).
- `test_ab_prompt_is_blind` — prompt names the taxon, asks A/B, and does NOT leak "baseline"/"refined"/round.
- `test_judge_pair_parses_A_B` — fake vision_fn returning "A"/"B"/junk → "A"/"B"/None.
- `test_verdict_both_orders` — refined-both → "refined"; baseline-both → "baseline"; a flip → "inconsistent".
- `test_aggregate_rates` — buckets + refined_rate over the A/B denominator (excludes repair/no-refinement).
- `test_dgen_baseline_persist` — `refine_loop` with a fake run*fn writes `dgen_baseline/{run}*{taxon}.glb` for a
  valid round 0, and does not for an invalid round 0.

The live A/B run + the 2 fresh D-Gen model runs are the real-execution step (OpenRouter + Blender + browser),
reported in the results doc.

## Global constraints

- Test runner `.venv/bin/pytest`; **never** `BIO3D_DATABASE_URL=study`. Baseline 629 passed / 8 skipped.
- Reuse verbatim: `agentic.vision_complete` (import, do not edit — it's another agent's file),
  `judge_render.tile_contact_sheet` + `CONDITIONS["multi4"]`, `judge_capture.browser_capture_multi_factory`,
  `dgen.refine_loop` (extended only with the baseline-persist), `service.dgen_trajectory`, `commission.run_bpy`.
- Injected `vision_fn`/capture/run seams so unit tests never touch network/browser/Blender/VLM.
- Independent judge disjoint from tested generators + different lab from the `claude-sonnet-4-6` generation judge.
- A/B is blind (no baseline/refined labels to the judge) and runs both orders.
