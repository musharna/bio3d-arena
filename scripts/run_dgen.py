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
    from app.models import DGenRun, Task, TraitRubric

    summaries = []
    with SessionLocal() as db:
        run = DGenRun(model_id=args.model)
        db.add(run)
        db.flush()
        run_id = run.id
        for taxon, common in SPECIES_COMMON.items():
            rubric = db.query(TraitRubric).filter_by(taxon=taxon).first()
            if rubric is None or not rubric.task_id:
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
            summaries.append(summary)
        db.commit()

    for s in summaries:
        print(s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
