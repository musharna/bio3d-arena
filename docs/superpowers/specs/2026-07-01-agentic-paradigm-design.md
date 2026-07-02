# Agentic 3D paradigm (`agentic`) — Design Spec

> Created 2026-07-01. Third per-paradigm track (after procedural_llm scorecard/vote-board and
> text_native). Fills the reserved `agentic` slot ("Agentic 3D"). Video deferred.

## Problem

`procedural_llm` is **one-shot**: the LLM writes a Blender-Python script, we run it once. The
reserved `agentic` paradigm — an LLM that **iteratively refines** its 3D via visual feedback — is
unfilled. Open scientific question the arena can answer: **does render→critique→revise iteration
beat one-shot?** No agentic 3D-generation arena exists for plants.

## Goal

Build the `agentic` paradigm: an LLM builds a plant mesh, sees its own render, critiques it against
the target species, and revises its script — N iterations. Outputs enter the arena (vote pool +
`agentic` leaderboard group), **directly comparable to procedural_llm (same underlying models)**.
Reuse the commission harness. No new DB table. Bounded LLM spend.

## Decisions (locked in brainstorming)

1. **Approach A** — render→critique→revise loop (NOT a tool-use agent; that's a much larger build).
2. **Reuse `app/commission.py`**: `openrouter_complete`, `run_bpy`, `is_valid_mesh`,
   `extract_script`, `get_or_create_generator`, `_sandbox_env`.
3. **Critique render = self-contained headless Blender GLB→PNG** (`blender --background --python`),
   NOT the Playwright/model-viewer path (no server dependency inside the loop).
4. **Vision critique = a new vision-capable OpenRouter call** — `openrouter_complete` sends a
   text-only `content` string; the critique needs the OpenAI vision `content` list
   (`[{type:text},{type:image_url, image_url:{url:"data:image/png;base64,..."}}]`, which OpenRouter
   supports). Add `vision_complete(...)` rather than overload the existing function.
5. **Roster = 3 strong vision models** (`anthropic/claude-opus-4.8`, `google/gemini-3.1-pro-preview`,
   `openai/gpt-5.1`); **N = 2 iterations** by default (iter-0 generate + 1 revise), `--iters` configurable.
6. **Source tag `agentic:<model_id>`**; `classify()` rule `agentic:` → `agentic`. **No new table** —
   iteration metadata (`n_iterations`, per-iteration vertex counts, `modality:"agentic"`) in
   `ModelOutput.meta_json`. Generator slug distinct from procedural_llm's (`agentic-<model>`) so the
   two paradigms are separate generators.
7. **Idempotent** (skip a `(task, model)` that already has an `agentic:` output) + **`--no-score`
   default** — mirrors `generate_api_text` (scoring is a separate follow-up).

## A. Core loop — `app/agentic.py`

- `render_glb_png(glb_path, *, blender_bin="blender", timeout_s=120) -> bytes`: run a small bpy
  render script headless — import the GLB, frame all geometry, add a sun + a 3/4 camera, render
  ~512² PNG to a temp path, return bytes. Self-contained; no app server.
- `vision_complete(post, model_id, prompt, image_png, *, api_key, max_tokens=32000, max_retries=3)
-> str`: one OpenRouter vision completion; `content` is `[{type:text,text:prompt},
{type:image_url,image_url:{url:"data:image/png;base64,<b64>"}}]`. Same retry/backoff shape as
  `openrouter_complete`.
- `critique_prompt(species, common) -> str`: "Here is your current 3D mesh of {common} ({species})
  rendered. Compare it to a real {common}; identify what's wrong (proportions, missing organs,
  topology); output ONLY an improved complete bpy script (same OUT_GLB contract)."
- `agentic_generate(db, *, model_id, task, species, common, complete_fn, vision_fn, run_fn,
render_fn, asset_dir, n_iters=2) -> dict`:
  1. iter 0: `script = extract_script(complete_fn(build_prompt(species, common)))`; `run_fn` → GLB;
     `is_valid_mesh`. If invalid after iter 0 → record an error attempt and return (no output).
  2. iters 1..n-1: `png = render_fn(current_glb)`; `new = extract_script(vision_fn(critique_prompt,
png))`; `run_fn` → GLB2; if valid → adopt GLB2 (record its vertices); else keep previous
     (never regress below a valid mesh).
  3. Ingest `ModelOutput(source=f"agentic:{model_id}", meta={"modality":"agentic",
"n_iterations": k, "iter_vertices":[...], "provider": model_id})` via a distinct
     `agentic-<slug>` generator. Return a per-model report.

## B. Generation script — `scripts/generate_agentic.py`

`main()` wires the real `httpx.post`, `run_bpy`, `render_glb_png`, `vision_complete`, the 3-model
roster, and the 6 taxa (reuse the taxon list shape from `generate_api_text`). Flags:
`--crop <substr>`, `--iters N`, `--no-score`. Idempotent per `(task, model)`. Commits per output.

## C. Classification + integration

- `scripts/backfill_paradigms.py::classify`: add `if any_src_prefix("agentic:"): return "agentic"`
  (distinct prefix; order-independent of the `api:`/`api:text:` rules).
- Outputs flow into the existing vote pool (the #3 matchmaking fix already rotates all paradigms)
  and the leaderboard's `agentic` group; thumbnails via `render_thumbnails.py`; backfill assigns
  `paradigm=agentic`.

## Testing

- `agentic_generate` with injected fakes: (a) iter-0 valid + 1 valid revise → `n_iterations==2`,
  adopts revised (vertices differ), `source=="agentic:<m>"`; (b) revise invalid → keeps iter-0
  (fallback, `n_iterations==1`); (c) iter-0 invalid → error attempt, no ModelOutput.
- `vision_complete` builds the correct vision `content` list (data-URI image_url) — asserted on the
  injected `post` payload; key in header only.
- `classify("agentic-x", "model", {"agentic:openai/gpt-5.1"}) == "agentic"`; regression: `api:` and
  `api:text:` unchanged.
- Idempotency: second run for same `(task, model)` → `skipped_exists`, no duplicate.
- **Real-execution check:** `render_glb_png` on one real GLB → non-empty PNG (Blender live).
- Full suite green.

## Risks / non-goals

- Each iteration adds a Blender run + a render (~seconds) + a vision call; N=2 keeps it bounded.
- Fallback guarantees the output is never worse than the last valid mesh (a failed revise can't
  regress the result).
- Vision models must accept images — roster chosen accordingly.
- **Not in scope:** tool-use agent (approach B), video→3D, recon/trait scoring integration
  (separate), default N>2. Small n; the value is the agentic-vs-procedural_llm comparison.
