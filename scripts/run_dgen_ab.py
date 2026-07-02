# scripts/run_dgen_ab.py
"""Independent cross-judge A/B for D-Gen firming. For each DGenRun, blind-compare round-0 baseline
vs refined-best per taxon with a DIFFERENT-lab VLM (default openai/gpt-5.1) via OpenRouter vision,
both orders, and write a results doc. Build the judge from OPENROUTER_API_KEY. Renders via Playwright.
NEVER set BIO3D_DATABASE_URL=study — point at a COPY of the study DB.

Prereq: the D-Gen runs exist in the target DB (run scripts/run_dgen.py for the tested models first),
and round-0 baseline GLBs exist under {ASSET_DIR}/dgen_baseline/. For the pre-existing gemini run whose
baselines predate the baseline-persist, pass --backfill-from-tmp to copy its dgen_tmp/<run>_<taxon>_r0.glb
into dgen_baseline/ first."""

from __future__ import annotations

import argparse
import functools
import os
import sys

from app.database import SessionLocal, init_db

RESULTS = "docs/results/2026-07-02-dgen-firming-results.md"


def _backfill_from_tmp(asset_dir: str) -> int:
    """Copy dgen_tmp/<run>_<taxon>_r0.glb -> dgen_baseline/<run>_<taxon>.glb (for pre-persist runs)."""
    import glob
    import os.path
    import re
    import shutil

    tmp = os.path.join(asset_dir, "dgen_tmp")
    base = os.path.join(asset_dir, "dgen_baseline")
    os.makedirs(base, exist_ok=True)
    n = 0
    for p in glob.glob(os.path.join(tmp, "*_r0.glb")):
        m = re.match(r"(.+)_r0\.glb$", os.path.basename(p))
        if not m:
            continue
        dst = os.path.join(base, m.group(1) + ".glb")
        if not os.path.exists(dst):
            shutil.copyfile(p, dst)
            n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description="Independent cross-judge A/B for D-Gen firming.")
    ap.add_argument(
        "--judge-model", default="openai/gpt-5.1", help="independent A/B judge (OpenRouter id)"
    )
    ap.add_argument("--run-id", type=int, default=None, help="limit to one DGenRun (default: all)")
    ap.add_argument(
        "--backfill-from-tmp",
        action="store_true",
        help="seed dgen_baseline/ from dgen_tmp round-0 GLBs first",
    )
    args = ap.parse_args()

    import httpx

    from app.agentic import vision_complete
    from app.config import ASSET_DIR
    from app.dgen_ab import (
        ab_work,
        aggregate,
        composite_ab,
        judge_pair,
        render_sheet,
        verdict_both_orders,
    )
    from app.models import DGenRun
    from app import service
    from scripts.judge_capture import browser_capture_multi_factory

    or_key = os.environ["OPENROUTER_API_KEY"]
    capture_multi = browser_capture_multi_factory()
    vision_fn = functools.partial(vision_complete, httpx.post, args.judge_model, api_key=or_key)
    # vision_complete(post, model_id, prompt, image_png, *, api_key): partial leaves (prompt, image_png).

    asset_dir = str(ASSET_DIR)
    if args.backfill_from_tmp:
        print(f"backfilled {_backfill_from_tmp(asset_dir)} baseline GLBs from dgen_tmp", flush=True)

    init_db()
    all_rows = []
    with SessionLocal() as db:
        runs = [db.get(DGenRun, args.run_id)] if args.run_id else db.query(DGenRun).all()
        runs = [r for r in runs if r is not None]
        for run in runs:
            for w in ab_work(db, run.id, asset_dir):
                row = {"run_id": run.id, "model_id": run.model_id, **w}
                if w["bucket"] == "ab":
                    sheet_base = render_sheet(w["baseline_glb"], capture_multi)
                    sheet_best = render_sheet(w["best_glb"], capture_multi)
                    comp_ab = composite_ab(sheet_base, sheet_best)  # baseline=A
                    comp_ba = composite_ab(sheet_best, sheet_base)  # baseline=B
                    pick1 = judge_pair(vision_fn, comp_ab, w["taxon"], w["common"])
                    pick2 = judge_pair(vision_fn, comp_ba, w["taxon"], w["common"])
                    row["verdict"] = verdict_both_orders(pick1, pick2)
                    row["picks"] = [pick1, pick2]
                all_rows.append(row)
                print(
                    f"{run.model_id} {w['taxon']}: {row.get('bucket')} {row.get('verdict', '')}",
                    flush=True,
                )
        traj = service.dgen_trajectory(db)

    agg = aggregate(all_rows)
    lines = [
        "# D-Gen firming — independent cross-judge A/B + multi-model results",
        "",
        f"Independent judge: `{args.judge_model}` (different lab from the claude-sonnet-4-6 generation judge).",
        "",
        "## Independent cross-judge A/B (blind, both orders)",
        f"- A/B pairs (best_round>0, valid baseline): **{agg['n_ab']}**",
        f"- **refined preferred: {agg['refined']}/{agg['n_ab']}** (rate {agg['refined_rate']})",
        f"- baseline preferred: {agg['baseline']}/{agg['n_ab']}",
        f"- inconsistent (position-flip/tie): {agg['inconsistent']}/{agg['n_ab']}",
        f"- repairs (invalid baseline -> valid best, not A/B'd): {agg['repairs']}",
        f"- no-refinement (best == round 0): {agg['no_refinement']}",
        "",
        "### Per (model, taxon)",
    ]
    for r in all_rows:
        lines.append(
            f"- {r['model_id']} / {r['taxon']}: {r['bucket']} {r.get('verdict', '')} {r.get('picks', '')}"
        )
    lines += ["", "## Multi-model same-judge fidelity lift (sonnet judge, from D-Gen runs)"]
    for t in traj:
        lines.append(
            f"- {t['model_id']} / {t['taxon']}: r0={t['fidelity_0']} best={t['fidelity_best']} lift={t['lift']}"
        )
    lines += [
        "",
        "## Caveats",
        "- The A/B tests only where refinement CHANGED the output (best_round>0); repairs + no-refinement are",
        "  reported separately so the denominator is honest.",
        "- Per-taxon n is small. Chamfer is intentionally NOT the primary axis (geometry != morphology).",
    ]
    os.makedirs(os.path.dirname(RESULTS), exist_ok=True)
    with open(RESULTS, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"wrote {RESULTS}: {agg}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
