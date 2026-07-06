# scripts/run_dgen.py
"""Run the D-Gen rubric-in-the-loop refinement for one model over the 6 taxa. Reuses the
commission harness (LLM + Blender), the trait judge, and the completeness metric. Build the
Anthropic judges from ANTHROPIC_API_KEY and the LLM from OPENROUTER_API_KEY. Renders GLBs
directly via Playwright. NEVER set BIO3D_DATABASE_URL=study — point at a throwaway/copy DB."""

from __future__ import annotations

import argparse
import functools
import os
import sys

# bootstrap: allow `python scripts/<name>.py` without PYTHONPATH (repo root on sys.path)
import sys as _sys
import pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parent.parent))

from app.database import SessionLocal, init_db
from app.commission import SPECIES_COMMON, openrouter_complete, run_bpy
from app.dgen import refine_loop, score_glb


def _complete_fn(post, api_key):
    def complete(model_id, prompt):
        return openrouter_complete(post, model_id, prompt, api_key=api_key)

    return complete


def _run_fn(script, out_glb):
    return run_bpy(script, out_glb=out_glb)


def main() -> int:
    ap = argparse.ArgumentParser(description="Run D-Gen refinement for one model over the taxa.")
    ap.add_argument(
        "--model", required=True, help="OpenRouter model id (e.g. google/gemini-2.5-pro)"
    )
    ap.add_argument("--max-rounds", type=int, default=3)
    ap.add_argument(
        "--run-id",
        type=int,
        default=None,
        help="resume an existing DGenRun id; taxa that already have iterations are skipped",
    )
    args = ap.parse_args()

    import anthropic
    import httpx

    from scripts.judge_capture import browser_capture_multi_factory

    or_key = os.environ["OPENROUTER_API_KEY"]
    judge_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    capture_multi = browser_capture_multi_factory()
    complete_fn = _complete_fn(httpx.post, or_key)

    init_db()
    from app.config import ASSET_DIR
    from app.models import DGenIteration, DGenRun, Task, TraitRubric

    with SessionLocal() as db:
        if args.run_id is not None:
            run = db.get(DGenRun, args.run_id)
            if run is None:
                print(f"run-id {args.run_id} not found", file=sys.stderr)
                return 2
            run_id = run.id
        else:
            run = DGenRun(model_id=args.model)
            db.add(run)
            db.commit()  # persist the run row immediately so a resume can find it
            run_id = run.id
        print(f"dgen run_id={run_id} model={args.model} max_rounds={args.max_rounds}", flush=True)

        for taxon, common in SPECIES_COMMON.items():
            # Resume: a taxon with any committed iteration for this run is already done — skip it.
            if db.query(DGenIteration).filter_by(run_id=run_id, taxon=taxon).first() is not None:
                print(f"skip {taxon}: already has iterations for run {run_id}", flush=True)
                continue
            rubric = db.query(TraitRubric).filter_by(taxon=taxon).first()
            if rubric is None or not rubric.task_id:
                print(f"skip {taxon}: no TraitRubric/task_id", file=sys.stderr, flush=True)
                continue
            import json as _json

            traits = _json.loads(rubric.traits_json or "[]")
            task = db.get(Task, rubric.task_id)
            prompt = task.prompt if task else ""
            score_fn = functools.partial(
                score_glb,
                taxon=taxon,
                prompt=prompt,
                traits=traits,
                capture_multi=capture_multi,
                trait_client=judge_client,
                completeness_client=judge_client,
            )
            summary = refine_loop(
                db,
                run_id=run_id,
                taxon=taxon,
                task_id=rubric.task_id,
                prompt=prompt,
                common=common,
                model_id=args.model,
                traits=traits,
                complete_fn=complete_fn,
                run_fn=_run_fn,
                score_fn=lambda glb, _f=score_fn: _f(glb),
                asset_dir=str(ASSET_DIR),
                max_rounds=args.max_rounds,
            )
            db.commit()  # PER-TAXON commit: a kill only loses the in-progress taxon
            print(f"done {taxon}: {summary}", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
