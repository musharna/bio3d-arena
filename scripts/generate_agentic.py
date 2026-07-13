"""Generate agentic 3D outputs (render->critique->revise) per (model, taxon) and ingest them.
`run_agentic_batch` is the testable core (fns injected); `main()` wires the real OpenRouter +
Blender + render + roster. Key-gated: needs OPENROUTER_API_KEY. Study data is runtime, not committed."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import agentic, commission  # noqa: E402

ROSTER = [
    "anthropic/claude-opus-4.8",
    "google/gemini-3.1-pro-preview",
    "openai/gpt-5.1",
]


def run_agentic_batch(
    db,
    *,
    roster,
    taxon_tasks,
    complete_fn,
    vision_fn,
    run_fn,
    render_fn,
    asset_dir,
    n_iters: int = 2,
    crop: str | None = None,
) -> dict:
    """For each (model, (species, task_id)): agentic_generate. complete_fn(model_id, prompt)->str;
    vision_fn(model_id, prompt, png)->str. Skips existing (idempotent via agentic_generate)."""
    counts = {"ok": 0, "skipped_exists": 0, "error": 0, "invalid_mesh": 0, "timeout": 0}
    for species, task_id in taxon_tasks:
        if crop and crop.lower() not in species.lower():
            continue
        common = commission.SPECIES_COMMON.get(species, species)
        for model_id in roster:
            try:
                rep = agentic.agentic_generate(
                    db,
                    model_id=model_id,
                    task_id=task_id,
                    species=species,
                    common=common,
                    complete_fn=lambda prompt, _m=model_id: complete_fn(_m, prompt),
                    vision_fn=lambda prompt, png, _m=model_id: vision_fn(_m, prompt, png),
                    run_fn=run_fn,
                    render_fn=render_fn,
                    asset_dir=asset_dir,
                    n_iters=n_iters,
                )
            except Exception as e:  # noqa: BLE001 — one model's failure shouldn't abort the batch
                counts["error"] += 1
                print(f"  {species} / {model_id}: ERROR {type(e).__name__}: {e}")
                continue
            counts[rep["status"]] = counts.get(rep["status"], 0) + 1
            print(
                f"  {species} / {model_id}: {rep['status']}"
                + (f" (iters={rep.get('n_iterations')})" if rep["status"] == "ok" else "")
            )
    return counts


def main() -> int:
    import os

    import httpx

    from app import config
    from app.database import SessionLocal

    ap = argparse.ArgumentParser(
        description="Generate agentic (render-critique-revise) 3D outputs."
    )
    ap.add_argument("--crop", default=None, help="substring of a species to run just one taxon")
    ap.add_argument("--iters", type=int, default=2, help="iterations per output (>=1)")
    args = ap.parse_args()

    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        print("no OPENROUTER_API_KEY in env — nothing to generate")
        return 0

    def complete_fn(model_id, prompt):
        return commission.openrouter_complete(httpx.post, model_id, prompt, api_key=key)

    def vision_fn(model_id, prompt, png):
        return agentic.vision_complete(httpx.post, model_id, prompt, png, api_key=key)

    def run_fn(script, out_glb):
        return commission.run_bpy(script, out_glb=out_glb)

    # Prove Blender runs before spending LLM calls: run_bpy maps a non-zero exit to the MODEL, so
    # an unrunnable Blender would be recorded as every model failing the task.
    commission.preflight_sandbox()

    db = SessionLocal()
    try:
        taxon_tasks = commission.resolve_taxon_tasks(db)
        counts = run_agentic_batch(
            db,
            roster=ROSTER,
            taxon_tasks=taxon_tasks,
            complete_fn=complete_fn,
            vision_fn=vision_fn,
            run_fn=run_fn,
            render_fn=agentic.render_glb_png,
            asset_dir=str(config.ASSET_DIR),
            n_iters=args.iters,
            crop=args.crop,
        )
        print(counts)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
