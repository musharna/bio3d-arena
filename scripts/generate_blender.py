"""Generate a procedural tomato with a native Blender (bpy) generator and ingest it as a
'procedural:blender' entry. `ingest_blender` is the testable core (GLB bytes + scorer injected);
`main()` runs Blender headless on `blender/gen_tomato.py` (which exports a GLB directly — no OBJ
step), then ingests. Commits per object.

The Blender tomato cleared the independent critic gate (pinnately compound serrated leaves with
real thickness + red fruit trusses + upright habit), so it carries NO caveat — like L-Py, unlike
Helios/AgriGen. This is the DCC/game-asset procedural representative of the field.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app import ingest  # noqa: E402
from app.mesh_convert import MeshConvertError  # noqa: E402
from app.models import Task  # noqa: E402

TOMATO_TITLE = "Solanum lycopersicum — single-image → 3D reconstruction"
BLENDER_LICENSE = "Blender bpy procedural (GPL); tomato generator authored for bio3d-arena"
BLENDER_URL = "https://www.blender.org"


def ingest_blender(
    db, glb_paths, *, to_glb, score_fn=None, variant="tomato", task_title=TOMATO_TITLE, limit=10
) -> dict:
    task = db.execute(select(Task).where(Task.title == task_title)).scalars().first()
    if task is None:
        raise RuntimeError(f"subject task not found: {task_title!r}")
    report = {"hosted": 0, "skipped": 0, "errors": 0, "by_variant": {}}
    for path in list(glb_paths)[:limit]:
        asset_id = Path(path).stem
        try:
            try:
                glb = to_glb(path)
            except MeshConvertError as e:
                print(f"  skip {asset_id}: {e}")
                report["skipped"] += 1
                continue
            out, _created = ingest.register_output(
                db,
                task_id=task.id,
                generator_slug="blender",
                generator_name="Blender (procedural)",
                data=glb,
                ext="glb",
                title=asset_id,
                meta={"depiction": "whole_plant", "variant": variant, "render": "mesh"},
            )
            out.source = "procedural:blender"
            out.license = BLENDER_LICENSE
            out.attribution = f"Authored Blender bpy procedural tomato [{variant}]"
            out.external_url = BLENDER_URL
            db.commit()  # provenance committed → hosted
            report["hosted"] += 1
            report["by_variant"][variant] = report["by_variant"].get(variant, 0) + 1
            if score_fn is not None:
                try:
                    score_fn(db, out)
                    db.commit()
                except Exception as e:  # noqa: BLE001 — scoring best-effort; object stays hosted
                    print(f"  score failed for {out.id}: {e}")
                    try:
                        db.rollback()
                    except Exception:  # noqa: BLE001
                        pass
        except Exception as e:  # noqa: BLE001 — one bad asset never aborts the batch
            print(f"  error {asset_id}: {e}")
            report["errors"] += 1
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                pass
    return report


def main() -> int:
    import argparse
    import os
    import subprocess
    import tempfile

    from app import recon_service
    from app.database import SessionLocal

    repo = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--blender",
        default=os.environ.get("BLENDER_BIN", str(Path.home() / "blender/blender")),
        help="path to the Blender binary (or set BLENDER_BIN)",
    )
    ap.add_argument("--script", default=str(repo / "blender/gen_tomato.py"))
    ap.add_argument("-n", type=int, default=1, help="number of seeds/plants to generate")
    ap.add_argument("--no-score", action="store_true")
    args = ap.parse_args()

    blender = Path(args.blender)
    if not blender.exists():
        print(f"Blender binary not found: {blender} — set BLENDER_BIN")
        return 1
    if not Path(args.script).exists():
        print(f"Blender generator script not found: {args.script}")
        return 1

    out_dir = tempfile.mkdtemp(prefix="blender_")
    glbs = []
    for seed in range(args.n):
        glb = str(Path(out_dir) / f"tomato_{seed}.glb")
        try:
            subprocess.run(
                [str(blender), "-b", "-P", args.script, "--", glb, str(seed)],
                check=True,
                timeout=300,
            )
            if Path(glb).exists():
                glbs.append(glb)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
            print(f"  blender seed {seed} failed: {e}")
    if not glbs:
        print(f"no .glb produced under {out_dir}")
        return 1

    db = SessionLocal()
    try:
        report = ingest_blender(
            db,
            glbs,
            to_glb=lambda p: Path(p).read_bytes(),  # Blender exports GLB natively
            score_fn=None if args.no_score else recon_service.score_and_store,
        )
        print(report)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
