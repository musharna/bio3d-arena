# D-Gen — rubric-in-the-loop self-improving generation (v1 design)

**Status:** approved design, 2026-07-02. Feeds `writing-plans`.

## Goal

Close the loop between the arena's **evaluators** (trait-morphology rubric judge + the completeness
metric) and its **LLM-procedural generator** (the commissioned "LLM writes Blender-Python" harness):
generate → render → score → build an actionable critique → **feed the critique + the previous script
back** → regenerate, for up to 3 rounds per taxon, stopping on plateau. Measure whether rubric
feedback raises morphology fidelity, and promote the best result into the arena.

Grounded OWNED-AT-SEAM in `next_directions_triage_2026-07-01`. Nearest owner: FloraForge
(2512.11925 — LLM+L-system maize refined by Chamfer-to-scan + human loop). **Our seam:** rubric-driven,
per-trait _interpretable_ feedback across **6 taxa**, reusing the arena's evaluator stack (trait
rubric + completeness) as the reward — richer than Chamfer-to-one-scan, and the rubrics/harness
already exist (the moat).

## Scope (locked)

- **Reward:** trait-morphology rubric fidelity (primary) + completeness as a gate. Fidelity =
  `present_correct / assessable` over the taxon's rubric traits (assessable = verdict not
  `not_assessable`).
- **Breadth:** **1** strong code-writing model × **all 6** taxa × **up to 3** rounds, **stop on
  plateau** (a round that doesn't beat the best fidelity so far ends that taxon; keep the best round).
- **v1 is a research experiment that also ships arena data:** it produces a fidelity-trajectory
  result doc AND ingests the best refined output per taxon as a votable arena output.

**Non-goals (YAGNI):** no live-UI refinement loop, no multi-model sweep (v2), no fine-tuning/RL
(prompt-feedback only), no new evaluator, no change to the vote/matchmaking logic.

## Key architectural decision: score GLBs directly, no per-round arena outputs

The production capture factory `scripts/judge_capture.browser_capture_multi_factory()` returns
`capture_multi(glb_abs, azimuths, elev) -> list[bytes]` — it renders a **GLB path directly**, no DB
row. So D-Gen renders + scores each round's GLB **without** creating a `ModelOutput` per round
(which would otherwise pollute the vote pool and boards). Only the **best** round is ingested as a
real `ModelOutput`. This keeps the loop self-contained and touches no sourcing/vote-pool code.

## Data flow (per taxon, ≤3 rounds)

```
round 0:  prompt = build_prompt(taxon, common)                      # reuse commission harness
          script = extract_script(complete_fn(model_id, prompt))
          run    = run_fn(script, out_glb)                          # run_bpy sandbox -> GLB
          score  = score_glb(glb, taxon, traits)                    # capture_multi -> sheet -> judges
          -> DGenIteration(round=0, fidelity, completeness, critique, script, status)

round n:  critique = build_critique(prev.trait_results, prev.completeness, prev.run_status, traits)
          prompt   = build_refine_prompt(taxon, common, prev.script, critique)
          ... generate -> run -> score -> DGenIteration(round=n, ...)
          stop if fidelity <= best_so_far (plateau) OR n == max_rounds

after:    best = argmax fidelity over rounds (completeness-gated: a `complete` output outranks a
          non-complete one at equal fidelity); ingest best GLB as ModelOutput(source="dgen-best")
          under generator "<model>-dgen" (paradigm procedural_llm); set the promoted DGenIteration's
          output_id.
```

The critique also carries **execution errors**: if a round's `run_status != "ok"` (invalid_mesh /
error / timeout), the critique includes the stderr, so the loop repairs broken scripts too — it
improves validity (pass@1), not only fidelity.

## Components

### `app/dgen.py` (new — pure logic + injected client/capture seams)

```python
def fidelity(trait_results: list[dict]) -> tuple[float | None, int, int]:
    """(fidelity, n_correct, n_assessable) from check_traits output. assessable = verdict !=
    'not_assessable'. fidelity = n_correct/n_assessable (present_correct only), or None if 0 assessable."""

def build_critique(trait_results: list[dict], traits: list[dict], completeness: dict,
                   run_status: str, run_error: str) -> str:
    """Actionable instruction list: for each present_wrong/absent trait -> 'FIX <key>: expected
    <expected>'; missing required organs from completeness (category != 'complete'); and, if
    run_status != 'ok', the execution error to repair. Empty string only if nothing to fix."""

def build_refine_prompt(species: str, common: str, prev_script: str, critique: str) -> str:
    """base build_prompt(species, common) + the previous script + the critique + 'Revise the script
    to fix exactly these issues. Output ONLY the revised Python script.'"""

def score_glb(glb_path, *, taxon: str, prompt: str, traits: list[dict], capture_multi,
              trait_client, completeness_client, condition: str = "multi4") -> dict:
    """Render the GLB directly (capture_multi -> tile_contact_sheet) then score:
    check_traits(trait_client, species=taxon, prompt=prompt, sheet_b64=..., traits=traits) and
    score_completeness(completeness_client, sheet_png, inventory=inventory_for(taxon)) -> derive.
    `prompt` is the taxon's Task.prompt (the same generation prompt the trait judge passes as
    context). Returns {fidelity, n_correct, n_assessable, trait_results, completeness_category,
    completeness_score, completeness_organs, sheet_png}. Raises on render failure (caller records)."""

def refine_loop(db, *, run_id: int, taxon: str, task_id: int, prompt: str, model_id: str,
                traits: list[dict], complete_fn, run_fn, score_fn, asset_dir,
                max_rounds: int = 3) -> dict:
    """Orchestrate rounds for ONE taxon. complete_fn(model_id, prompt)->str, run_fn(script,out_glb)->run
    dict (both mirror commission.run_batch's injected seams), score_fn(glb_path)->score dict (wraps
    score_glb with the bound clients/capture/prompt). Persists a DGenIteration per round; applies the
    stop rule + best-selection below; ingests the best VALID round's GLB as a ModelOutput and sets
    that iteration's output_id + is_best. Returns a per-taxon trajectory summary."""
```

**Stop rule + best-selection (explicit):**

- A round is _valid_ if `run_status == "ok"` AND `fidelity is not None`.
- **Round 0 always runs.** After each round `n >= 1`: **stop** (plateau) iff round `n` is _valid_ AND
  `fidelity(n) <= best_valid_fidelity_so_far`. A **failed/None round does NOT trigger plateau-stop** —
  the loop keeps going (up to `max_rounds`) so it can repair a broken script; it stops early only on a
  genuine valid-but-not-better round, or at `max_rounds`.
- **Best round** = the _valid_ round with the highest `fidelity`; ties broken by (1) `completeness_category
== "complete"` preferred, then (2) earliest round (fewest LLM calls). If **no** round is valid, nothing
  is promoted (`output_id` stays null on every iteration; the run records the failed trajectory).

Reused verbatim (grounded live): `commission.build_prompt/openrouter_complete/run_bpy/extract_script/
get_or_create_generator`; `traits.check_traits(client, *, species, prompt, sheet_b64, traits)->list[
{trait_key,trait_class,verdict,rationale}]` (verdict ∈ present_correct|present_wrong|absent|
not_assessable); `judge_render.tile_contact_sheet(pngs, cols, rows)`; `completeness.score_completeness`

- `derive`; `organ_inventory.inventory_for`; `judge_capture.browser_capture_multi_factory`.

### Persistence — `app/models.py`

```python
class DGenRun(Base):           # one driver run
    __tablename__ = "dgen_run"
    id; model_id: str; created

class DGenIteration(Base):     # one row per (run, taxon, round)
    __tablename__ = "dgen_iteration"
    __table_args__ = (UniqueConstraint("run_id", "taxon", "round", name="uq_dgen_iter"),)
    id
    run_id: FK dgen_run.id (index)
    taxon: str (index)
    round: int
    output_id: FK model_output.id (nullable — set only on the promoted best round)
    fidelity: float | None
    n_correct: int; n_assessable: int
    completeness_category: str          # complete|partial-organism|isolated-organ|fragment|""
    completeness_score: float | None
    critique: Text                       # the critique fed INTO this round ("" for round 0)
    script: Text
    status: str                          # ok|invalid_mesh|error|timeout|render_error
    is_best: bool (default False)
    created
```

Mirrors the one-row-per-thing style of `Metric`/`Completeness`/`TraitVerdict`. `init_db()`'s
`create_all` + additive self-heal creates the two tables on existing DBs.

### Best-output ingestion

Only the **best valid round** is persisted as an output; intermediate rounds are never `ModelOutput`s
(scored via direct GLB render), so nothing needs excluding from the vote pool. Ingestion of the best
round (one helper, `dgen.ingest_best`):

1. Copy the GLB to `{asset_dir}/dgen/<gen_slug>_<task_id>.glb`; create
   `ModelOutput(task_id, generator_id, asset_path, asset_format="glb", source="commissioned",
title="<model> (D-Gen r<k>)", meta_json={model_id, round, fidelity, dgen_run_id})`.
2. **Persist that round's evaluator results on the new output** so it flows through the _existing_
   aggregations with no board change: a `TraitVerdict` row per trait result (from the best round's
   `check_traits` output) and a `Completeness` row (via `completeness.upsert_completeness`).
3. Set the promoted `DGenIteration.output_id` + `is_best`.

The generator is `get_or_create_generator("<model>-dgen")` → slug `openrouter-<model>-dgen`, which the
paradigm backfill classifies `procedural_llm` (`classify`: `startswith("openrouter-")`). Using
`source="commissioned"` + persisted `TraitVerdict`s means `service.procedural_scorecard` computes its
`morph_fidelity` automatically, so the refined generator appears on the `/procedural` board **as a
distinct row next to the one-shot `openrouter-<model>` generator** — the refined-vs-one-shot
comparison, with zero board-code change. (The one-shot generators already exist from the commissioned
run.) The output is votable like any commissioned output.

### API — `app/main.py` + `app/service.py`

`service.dgen_trajectory(db, run_id: int | None = None) -> list[dict]` — per (run, taxon) the ordered
rounds `[{round, fidelity, completeness_category, status, is_best}]` + the round-0→best lift.
`GET /api/dgen.json` returns it (data-only, no new UI in v1).

### Driver — `scripts/run_dgen.py`

Iterate the 6 `SPECIES_COMMON` taxa (each resolved to its `task_id` + `Task.prompt` via `TraitRubric`
/`Task`), load each taxon's `traits` from `TraitRubric.traits_json`, bind `complete_fn` (OpenRouter
via `openrouter_complete`, model from `--model`/env), `run_fn` (`run_bpy`), and `score_fn` (`score_glb`
with a `browser_capture_multi_factory()` capture + an Anthropic client for both judges, and the
taxon's `prompt`). Create one `DGenRun`, call `refine_loop` per taxon, print the summary. Never set
`BIO3D_DATABASE_URL=study`.

## Validity guard (reward-hacking)

The reward _is_ the VLM rubric judge, so the headline claim is scoped honestly: **"rubric feedback
raises rubric fidelity."** Guards: (a) fixed judge model + prompt across all rounds; (b) the result
doc reports the **raw per-trait trajectory** per taxon (so gaming — e.g. fidelity rising while the
model degenerates — is visible); (c) an **independent spot-check** written up manually for 2–3 taxa
(round-0 vs best: a blind side-by-side, and Chamfer where a held-out scan exists). The circularity
caveat is stated explicitly. Automating the independent check is out of scope for v1.

## Success criterion + result

Mean rubric fidelity rises round-0 → best across the 6 taxa. `docs/results/2026-07-02-dgen-results.md`
reports: per-taxon trajectory (fidelity + completeness per round), aggregate lift, #taxa improved,
#exec-failures repaired, and the validity spot-check. Live run (OpenRouter + Blender + judges) is a
separate real-execution step, like the completeness validation.

## Testing

Unit tests with **fakes** (no LLM/Blender/VLM/browser), mirroring `commission.py`'s injected
`complete_fn`/`run_fn` seams and the `tests/test_trait_judge.py` DB pattern
(`init_db()` in `setup_module`, seed via `SessionLocal`):

- `test_dgen_fidelity` — `fidelity()` math: present_correct/assessable, not_assessable excluded, 0-assessable → None.
- `test_dgen_critique` — `build_critique` lists absent/present_wrong traits with their `expected`, missing organs, and the exec error when `run_status != "ok"`; empty when nothing to fix.
- `test_dgen_refine_prompt` — `build_refine_prompt` contains the base requirements, the previous script, and the critique.
- `test_dgen_loop_plateau_stop` — scripted `score_fn` with flat fidelity stops after the non-improving round; best round selected.
- `test_dgen_loop_improves_and_promotes` — scripted rising fidelity runs to `max_rounds`, best = last, `ingest_best` creates exactly one `ModelOutput(source="commissioned")` under the `openrouter-<model>-dgen` generator with per-trait `TraitVerdict` rows + one `Completeness` row, sets that iteration's `output_id`/`is_best`, and persists one `DGenIteration` per round.
- `test_dgen_loop_repairs_failed_round` — a failed round (`run_status="error"`, fidelity None) does NOT plateau-stop; the loop continues and a later valid round is promoted.
- `test_dgen_completeness_gated_best` — at equal fidelity, a `complete` round outranks a non-complete round; if no round is valid, nothing is promoted (no `ModelOutput`).
- `test_dgen_trajectory_api` — `service.dgen_trajectory` returns ordered rounds + lift.

Live real-execution run is the driver, reported in the results doc.

## Global constraints

- Test runner `.venv/bin/pytest`; **never** `BIO3D_DATABASE_URL=study`. Baseline 604 passed / 8 skipped.
- Reuse existing seams verbatim (§Components); do not reinvent the harness, judges, render, or paradigm classification.
- Injected client/capture/exec seams so unit tests never touch the network, a browser, Blender, or the VLM.
- The 6 taxa are exactly the keys of `commission.SPECIES_COMMON` == `trait_morphology.MORPHOLOGY_TRAITS`.
