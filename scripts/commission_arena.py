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
from app.rosters import PROCEDURAL_ROSTER  # noqa: E402


def taxon_tasks_for(db, crop: str | None) -> list[tuple[str, int]]:
    """The roster's (taxon, task_id) pairs, optionally narrowed to one species by substring.
    Each pair costs an LLM call plus a sandboxed Blender run, so a validation slice scopes to the
    taxa it is validating. Mirrors --crop in scripts/generate_agentic.py."""
    tt = commission.resolve_taxon_tasks(db)
    if crop:
        tt = [(s, t) for s, t in tt if crop.lower() in s.lower()]
        if not tt:
            raise SystemExit(f"--crop {crop!r} matched no taxon with a task")
    return tt


def plan(db, *, roster: list[str], crop: str | None = None, protocol: str = "repair") -> dict:
    """What the run will cost. Scoped to the SAME protocol run_batch will write under — a plan
    counted against a different protocol would report a call count the run does not make."""
    tt = taxon_tasks_for(db, crop)
    seen = commission.existing_pairs(db, protocol)
    needed = sum(1 for m in roster for _, tid in tt if (m, tid) not in seen)
    return {"tasks": len(tt), "roster": len(roster), "calls_needed": needed}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--roster",
        default=None,
        help="comma-separated OpenRouter model ids (default: app.rosters.PROCEDURAL_ROSTER)",
    )
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
    ap.add_argument("--crop", default=None, help="substring of a species, to run just one taxon")
    ap.add_argument(
        "--max-repairs",
        type=int,
        default=2,
        help=(
            "repair rounds allowed after a failing script, each handed the Blender traceback "
            "(default 2). 0 reproduces the old unaided-only protocol."
        ),
    )
    ap.add_argument(
        "--temperature",
        type=float,
        default=0.2,
        help=(
            "sampling temperature, pinned for every model (default 0.2). The first sweep sent "
            "none, so each cell ran at its provider's default and the board partly ranked noise."
        ),
    )
    ap.add_argument(
        "--protocol",
        default="repair",
        help="recorded on every row; the scorecard groups by it and excludes 'legacy'",
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    roster = (
        [m.strip() for m in args.roster.split(",") if m.strip()]
        if args.roster
        else list(PROCEDURAL_ROSTER)
    )

    with SessionLocal() as db:
        p = plan(db, roster=roster, crop=args.crop, protocol=args.protocol)
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
    # Prove the sandbox runs Blender before spending a single LLM call: a wrapper that cannot
    # start would otherwise be recorded as every model failing the task (and feed pass@1).
    commission.preflight_sandbox(sandbox_prefix=prefix, blender_bin=args.blender_bin)

    def complete_fn(model_id, prompt):
        return commission.openrouter_complete(
            httpx.post, model_id, prompt, api_key=api_key, temperature=args.temperature
        )

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
        tt = taxon_tasks_for(db, args.crop)
        res = commission.run_batch(
            db,
            complete_fn=complete_fn,
            run_fn=run_fn,
            roster=roster,
            taxon_tasks=tt,
            asset_dir=config.ASSET_DIR,
            max_calls=args.max,
            max_repairs=args.max_repairs,
            protocol=args.protocol,
        )
    print(res)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
