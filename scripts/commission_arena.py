"""Commissioned-generation arena driver: for each (model in roster) x (6-taxa task), ask the
model (via OpenRouter) for a bpy script, sandbox-run it, and ingest the result. Resumable
(skips attempted pairs), capped (--max), dry-run-able. Core logic lives in app/commission.py.

Env: OPENROUTER_API_KEY. Run against whatever DB BIO3D_DATABASE_URL points at (do NOT point
tests here). Mirrors scripts/trait_judge.py / scripts/scope_judge.py."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import commission, config  # noqa: E402
from app.database import SessionLocal  # noqa: E402


def plan(db, *, roster: list[str]) -> dict:
    tt = commission.resolve_taxon_tasks(db)
    seen = commission.existing_pairs(db)
    needed = sum(1 for m in roster for _, tid in tt if (m, tid) not in seen)
    return {"tasks": len(tt), "roster": len(roster), "calls_needed": needed}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--roster", required=True, help="comma-separated OpenRouter model ids")
    ap.add_argument("--blender-bin", default="blender")
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument(
        "--sandbox-prefix",
        default="heavy-run",
        help=(
            "command prefix for the untrusted bpy subprocess; default 'heavy-run' (mem cap). "
            "Use '' to disable, or e.g. 'heavy-run unshare -rn' to add network isolation."
        ),
    )
    ap.add_argument("--max", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    roster = [m.strip() for m in args.roster.split(",") if m.strip()]

    with SessionLocal() as db:
        p = plan(db, roster=roster)
        print(
            f"commission plan: {p['roster']} models x {p['tasks']} tasks; "
            f"{p['calls_needed']} calls needed"
        )
        if args.dry_run:
            return 0

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print("OPENROUTER_API_KEY not set", file=sys.stderr)
        return 2

    import httpx

    prefix = args.sandbox_prefix.split() or None

    def complete_fn(model_id, prompt):
        return commission.openrouter_complete(httpx.post, model_id, prompt, api_key=api_key)

    def run_fn(script, out_glb):
        return commission.run_bpy(
            script,
            out_glb=out_glb,
            timeout_s=args.timeout,
            blender_bin=args.blender_bin,
            sandbox_prefix=prefix,
        )

    config.ensure_dirs()
    with SessionLocal() as db:
        tt = commission.resolve_taxon_tasks(db)
        res = commission.run_batch(
            db,
            complete_fn=complete_fn,
            run_fn=run_fn,
            roster=roster,
            taxon_tasks=tt,
            asset_dir=config.ASSET_DIR,
            max_calls=args.max,
        )
    print(res)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
