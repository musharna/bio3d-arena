# Commissioned-Generation Arena — Design Spec (Harness, Spec #1)

> Created 2026-07-01. Parent: bio3d-arena. Follows the Mode-C calibration negative result
> (2026-07-01): grading a scraped/recon pile of models was confounded — ~40% of the 156
> models were structurally unfit to judge (24 not-a-plant + 34 organ-only + a degenerate
> tail). This project removes the confound at the source by commissioning fresh models on a
> common baseline.

## Problem

Mode-C tried to score botanical accuracy over a heterogeneous pile of scraped/recon models.
Human↔VLM calibration failed (κ < 0.4 in every framing) largely because a huge fraction of
models were ill-posed to grade (single fruits, junk blobs, degenerate whole-plants). We are
not measuring generators on a level field.

## Goal

Stand up a controlled, LM-arena-style generation benchmark: give every competing chat agent
the **same** plant-generation tasks, have each **author a Blender-Python (bpy) script**, run
those scripts in a sandbox, and ingest the resulting meshes as **agent-attributed** arena
outputs. These clean, well-posed models become fair inputs for the existing scoring/voting
infrastructure (scored in a follow-up Spec #2).

This spec covers **only the generation harness** — produce and ingest agent-tagged models
plus their execution outcomes. Scoring (execution/validity leaderboard, scope, Mode-C rubric
accuracy, head-to-head votes) is Spec #2, mostly wiring existing infra to the new outputs.

## Decisions (locked in brainstorming)

1. **Competitor = an LLM that writes procedural code.** Measures the model's ability to author
   a plant generator (botanical knowledge + coding), not a downstream gen-service's quality.
2. **Substrate = Blender Python (bpy), run headless.** LLMs are far more fluent in bpy than any
   plant DSL, so we measure modeling ability, not obscure-syntax recall. Native GLB export.
3. **Dispatch = OpenRouter** (single OpenAI-compatible key → Anthropic/OpenAI/Google/… models).
   The roster is a config list of model-id strings; provider-agnostic by construction.
4. **Baseline task set = the existing 6 taxa, 1 canonical prompt each** (Solanum lycopersicum,
   Zea mays, Pinus sylvestris, Rosa, Glycine max, Arabidopsis thaliana). Morphology rubrics +
   scope already exist for these, so Spec #2 scoring works day one.
5. **Prompt style = plain species prompt** (not a trait-by-trait spec) — the agent must supply
   the morphology from its own knowledge; that is the arena signal.
6. **Single-shot v1** — one completion per (model, task), no self-repair loop. A crash/invalid
   mesh is a recorded failure, not retried on bad code (only transport/HTTP errors retry).
   Agentic self-repair (feed the error back, N rounds) is an explicit future variant.
7. **Scoring axes (all four, in Spec #2):** execution & validity, species recognizability,
   morphology-rubric accuracy, head-to-head votes. Execution/validity is produced _in_ the
   harness (it is a byproduct of running the script); the rest are Spec #2.

## Architecture

```
prompt set (6 taxa) ──► OpenRouter dispatch (roster = model IDs)
  └► per (model, task): 1 completion → extract bpy script
       └► sandbox: blender --background --python  (timeout, mem cap, no network, temp cwd)
             └► capture status {ok|error|timeout|invalid_mesh} + export $OUT_GLB
                   └► ingest: Generator(per model) + ModelOutput(source="commissioned")
                        + CommissionAttempt(script, status, error, mesh stats, duration)
                              └► contact sheet (reuse judge_render) for Spec #2 scoring
```

## Components

New package `app/commission.py` (pure/core) + `scripts/commission_arena.py` (batch driver),
mirroring the `trait_judge`/`scope_judge` split (import-testable core, thin CLI):

- **Prompt set** — `TASK_PROMPTS: dict[taxon -> str]`; a builder `build_prompt(species, common)`
  producing the plain-species instruction that pins the output contract (`export GLB to
$OUT_GLB`, "output only the script").
- **Dispatch** — `openrouter_complete(client, model_id, prompt) -> str`. One OpenAI-compatible
  client (`base_url=https://openrouter.ai/api/v1`, `OPENROUTER_API_KEY`). Retries only on
  transport/5xx/429 (bounded), never on model content.
- **Script extraction** — `extract_script(completion_text) -> str` (pure): pull the Python code
  block; tolerate fenced/unfenced, leading prose. Empty/none → recorded as an `error` status.
- **Sandbox runner** — `run_bpy(script, *, out_glb, timeout_s, mem_mb) -> RunResult` where
  `RunResult = {status, stderr, duration_ms, glb_path|None}`. Runs
  `blender --background --python <runner>` with the script; the runner sets `$OUT_GLB` and
  execs the model's script. Isolation: temp cwd (only `$OUT_GLB` kept), wall-clock timeout,
  memory cap via the existing `heavy-run` cgroup, no network (`unshare -n` / equivalent).
- **Mesh validity** — `is_valid_mesh(glb_path) -> (bool, stats)` (pure over a file): GLB exists,
  non-empty, loads, has ≥1 mesh with vertices/faces. `stats` = vert/face/mesh counts, bbox.
- **Ingestion** — `ingest_attempt(db, task, model_id, run, script) -> CommissionAttempt`:
  get-or-create `Generator` (name `openrouter:<model_id>`), and on `ok` create a `ModelOutput`
  (glb asset, `source="commissioned"`, linked generator+task); always create a
  `CommissionAttempt` row (so failures are first-class). Then render a contact sheet.
- **Batch orchestrator** — `run_batch(db, *, complete_fn, run_fn, roster, tasks, max=None)`:
  resumable (skip existing (model_id, task) attempts), per-attempt commit, counts
  ok/error/timeout/invalid. `--dry-run` prints the (roster × tasks) call plan and exits.
  `complete_fn`/`run_fn` are injected so the orchestrator is testable without API/Blender.

## Data model

- **Reuse** `Task` (6 taxa tasks exist), `Generator`, `ModelOutput` (add nothing;
  `source="commissioned"` distinguishes them; existing Mode-A exclusion of scans/untextured is
  unaffected).
- **New** `CommissionAttempt` (create_all additive, no migration):
  `id, task_id (FK), model_id (str), generator_id (FK, nullable), output_id (FK, nullable),
status (str: ok|error|timeout|invalid_mesh), error (Text), script (Text),
mesh_stats_json (Text), duration_ms (int), created (dt)`. Unique `(model_id, task_id)` for
  resumability. A failed attempt has `output_id=NULL` but still records the script + error —
  execution-success rate is computed directly from these rows.

## Execution & sandbox (the main feasibility item)

LLM-authored bpy is untrusted code. Every run is confined:

- `blender --background --python runner.py` (Blender headless binary — installed separately;
  NOT the pip `bpy`, to stay independent of the venv's Python 3.13). Blender only needs to
  **export GLB**; rendering for the arena stays on the existing model-viewer path, so no GPU.
- Wall-clock `timeout_s` (default 120), memory cap via `heavy-run` cgroup (default 4 GB),
  network disabled, working dir a throwaway temp; only `$OUT_GLB` is harvested.
- Any nonzero exit / timeout / missing-or-invalid GLB maps to the corresponding failure status.

## Testing

- **Pure units:** `extract_script` (fenced/unfenced/prose/empty), `build_prompt` (contract
  present), roster/config parse, `is_valid_mesh` (valid GLB vs empty/no-geometry fixtures),
  `ingest_attempt` (ok → Generator+ModelOutput+Attempt; failure → Attempt only, output_id NULL).
- **Injected batch:** `run_batch` with fake `complete_fn`/`run_fn` → resumability (skips seen),
  status counting, per-attempt persistence, `--dry-run` plan.
- **Real-execution check (boundary):** one known-good bpy script actually run through the real
  `run_bpy` → produces a valid GLB. This validates Blender-on-WSL headless early, before any
  spend.

## Prerequisites (operator)

1. `OPENROUTER_API_KEY` set (create at openrouter.ai + add credit).
2. Blender headless binary installed and on PATH (implementation task; validated by the
   real-execution test).

## Cost

Tiny: |roster| × 6 tasks × 1 completion. E.g. 5 models × 6 = 30 short completions. Operator-
gated run; `--dry-run` first.

## Out of scope (→ Spec #2)

Scoring/leaderboard: execution-success leaderboard, scope classification of commissioned
outputs, Mode-C rubric accuracy on them, and head-to-head vote/BT ranking. All reuse existing
infra pointed at `source="commissioned"` outputs. Also future: self-repair loops, stage
variants, broader taxa, non-OpenRouter providers.

## Risks

- **Blender-on-WSL headless export** — primary feasibility risk; de-risked by the real-execution
  test as the first implementation step.
- **Untrusted code execution** — mitigated by the sandbox (no net, temp cwd, time+mem caps).
- **Very low execution-success rates** — possible if models write poor bpy; that is a valid
  measurement, not a failure of the harness. Recorded per attempt.
