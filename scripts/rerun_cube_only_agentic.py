#!/usr/bin/env python3
"""Re-run agentic outputs that are ONLY the leftover default cube.

Companion to the RUNNER_SRC empty-scene fix (`app/commission.py`) and
`strip_default_cube.py`. Some pre-fix agentic runs built *nothing valid*: the
model's Blender script produced no organism, but the export still succeeded
because Blender's startup scene ships a default cube — so the output passed
`is_valid_mesh` and was recorded as a real agentic result. Stripping such a file
would leave it empty, so those are excluded from the strip and handled here.

The right correction is to re-run them through the fixed harness: an empty scene
means "the model built nothing" now yields an invalid mesh, so `agentic_generate`
records NO output (a correct failure) instead of a scored cube — and if the model
*does* build an organism this time, we keep that.

Resumable by design. The set of targets is persisted to a manifest the first time
(so a run interrupted after the destructive delete can still finish — detection
alone can't recover a target whose cube GLB was already moved away). Each target
is processed idempotently:

  * asset present AND not cube-only  -> already done (a real organism); skip. This
    protects a good regeneration from being deleted on a resume.
  * otherwise (asset missing, or still cube-only) -> delete its ModelOutput row
    (+ admissibility) if present, move any cube GLB to the backup dir, then call
    `agentic_generate`. If the model still builds nothing, no row is written.

Dry-run by default. Requires OPENROUTER_API_KEY (loaded from .env). Point at the
study DB explicitly, e.g.:

    BIO3D_DATABASE_URL=sqlite:////abs/path/data/study/arena-study.db \\
    BIO3D_DATA_DIR=/abs/path/data \\
    python -u scripts/rerun_cube_only_agentic.py            # dry run
    ... same env ...  python -u scripts/rerun_cube_only_agentic.py --apply
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import trimesh  # noqa: E402
from sqlalchemy import text  # noqa: E402

from scripts.strip_default_cube import classify  # noqa: E402


def is_cube_only(path: Path) -> bool:
    scene = trimesh.load(str(path), force="scene")
    cubes, plants = classify(scene)
    return bool(cubes) and not plants


def resolve_target(db, filename: str) -> dict | None:
    """Build a manifest entry for a cube-only agentic GLB from its ModelOutput row."""
    row = db.execute(
        text(
            "SELECT mo.task_id, g.name FROM model_output mo "
            "JOIN generator g ON g.id = mo.generator_id WHERE mo.asset_path = :ap"
        ),
        {"ap": f"agentic/{filename}"},
    ).first()
    if row is None:
        return None
    task_id, gname = row
    taxon = db.execute(
        text("SELECT taxon FROM trait_rubric WHERE task_id = :t"), {"t": task_id}
    ).scalar()
    return {
        "filename": filename,
        "model_id": gname.removesuffix(" (agentic)"),
        "task_id": task_id,
        "taxon": taxon,
    }


def load_or_build_targets(db, asset_dir: Path, manifest: Path) -> list[dict]:
    """Read the target manifest if it exists (resume); otherwise detect the cube-only
    agentic GLBs, resolve each, and persist the manifest for a future resume."""
    if manifest.exists():
        return json.loads(manifest.read_text())
    targets = []
    for p in sorted(asset_dir.glob("agentic/*.glb")):
        if is_cube_only(p):
            t = resolve_target(db, p.name)
            if t is None:
                print(f"  WARN cube-only {p.name} has no DB row (orphan) — skipping")
                continue
            targets.append(t)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(targets, indent=1))
    return targets


def snapshot_db(db_path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = db_path.with_name(f"{db_path.name}.PRE-CUBEONLY-RERUN-{stamp}")
    src = sqlite3.connect(str(db_path))
    try:
        dst_conn = sqlite3.connect(str(dst))
        with dst_conn:
            src.backup(dst_conn)
        dst_conn.close()
    finally:
        src.close()
    return dst


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--apply", action="store_true", help="delete + regenerate (default: dry run)")
    ap.add_argument("--iters", type=int, default=2)
    args = ap.parse_args(argv)

    from app import agentic, commission, config
    from app.database import SessionLocal

    asset_dir = Path(config.ASSET_DIR)
    backup_dir = Path(config.DATA_DIR) / "_backups" / "cube_only_rerun"
    manifest = backup_dir / "targets.json"

    db = SessionLocal()
    try:
        targets = load_or_build_targets(db, asset_dir, manifest)

        # Partition into done vs pending (idempotent + resume-safe).
        pending = []
        for t in targets:
            p = asset_dir / "agentic" / t["filename"]
            if p.exists() and not is_cube_only(p):
                continue  # a good regeneration already exists — never touch it
            pending.append(t)

        print(
            f"targets: {len(targets)}  already-done: {len(targets) - len(pending)}  pending: {len(pending)}"
        )
        for t in pending:
            print(
                f"  PENDING {t['filename']}  model={t['model_id']}  task={t['task_id']}  taxon={t['taxon']!r}"
            )

        if not args.apply:
            print("\nDRY RUN (pass --apply to delete + regenerate)")
            return 0
        if not pending:
            print("\nnothing pending — all targets already resolved")
            return 0

        key = os.environ.get("OPENROUTER_API_KEY")
        if not key:
            print("no OPENROUTER_API_KEY in env — cannot regenerate", file=sys.stderr)
            return 1

        # config.DATABASE_URL is sqlite:////abs/path -> strip the scheme, keep the leading slash.
        db_path = Path(config.DATABASE_URL.split("sqlite:///", 1)[-1])
        print(f"\nsnapshot: {snapshot_db(db_path)}", flush=True)

        backup_dir.mkdir(parents=True, exist_ok=True)
        for t in pending:
            row = db.execute(
                text("SELECT id FROM model_output WHERE asset_path = :ap"),
                {"ap": f"agentic/{t['filename']}"},
            ).first()
            if row is not None:
                db.execute(text("DELETE FROM admissibility WHERE output_id = :o"), {"o": row[0]})
                db.execute(text("DELETE FROM model_output WHERE id = :o"), {"o": row[0]})
            src = asset_dir / "agentic" / t["filename"]
            if src.exists():
                shutil.move(str(src), str(backup_dir / t["filename"]))
        db.commit()
        print(
            f"cleared {len(pending)} pending outputs (rows + admissibility), cube GLBs -> {backup_dir}",
            flush=True,
        )

        import httpx

        def complete_fn(prompt, _m):
            return commission.openrouter_complete(httpx.post, _m, prompt, api_key=key)

        def vision_fn(prompt, png, _m):
            return agentic.vision_complete(httpx.post, _m, prompt, png, api_key=key)

        commission.preflight_sandbox()

        print("\nregenerating (fixed harness) ...", flush=True)
        results = []
        for t in pending:
            model_id, taxon = t["model_id"], t["taxon"]
            common = commission.SPECIES_COMMON.get(taxon, taxon)
            rep = agentic.agentic_generate(
                db,
                model_id=model_id,
                task_id=t["task_id"],
                species=taxon,
                common=common,
                complete_fn=lambda prompt, _m=model_id: complete_fn(prompt, _m),
                vision_fn=lambda prompt, png, _m=model_id: vision_fn(prompt, png, _m),
                run_fn=lambda script, out_glb: commission.run_bpy(script, out_glb=out_glb),
                render_fn=agentic.render_glb_png,
                asset_dir=str(asset_dir),
                n_iters=args.iters,
            )
            results.append((model_id, taxon, rep.get("status"), rep.get("n_iterations")))
            print(
                f"  {model_id} / {taxon}: {rep.get('status')} (iters={rep.get('n_iterations')})",
                flush=True,
            )

        ok = sum(1 for *_, s, _ in results if s == "ok")
        remaining = [p.name for p in sorted(asset_dir.glob("agentic/*.glb")) if is_cube_only(p)]
        print("\n=== summary ===")
        print(f"  regenerated real organism   : {ok}/{len(results)}")
        print(f"  recorded as failure (no row): {len(results) - ok}")
        print(f"  cube-only agentic GLBs remaining: {len(remaining)} {remaining}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
