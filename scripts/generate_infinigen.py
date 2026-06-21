"""Generate procedural plants with Infinigen and ingest them as 'Procedural' outputs.
`ingest_infinigen` is the testable core (OBJ->GLB + scorer injected); `main()` shells out
to a separate `infinigen` conda env to generate, then collects + ingests. Commits per object.
Infinigen is NOT a dependency of this app's venv — it runs in its own Python-3.11 env.
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
INFINIGEN_LICENSE = "BSD-3-Clause (Infinigen, Princeton VL)"
INFINIGEN_URL = "https://github.com/princeton-vl/infinigen"


def ingest_infinigen(
    db,
    obj_paths,
    *,
    to_glb,
    score_fn=None,
    factory="Succulent",
    task_title=TOMATO_TITLE,
    limit=10,
) -> dict:
    task = db.execute(select(Task).where(Task.title == task_title)).scalars().first()
    if task is None:
        raise RuntimeError(f"subject task not found: {task_title!r}")
    report = {"hosted": 0, "skipped": 0, "errors": 0, "by_factory": {}}
    for path in list(obj_paths)[:limit]:
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
                generator_slug="infinigen",
                generator_name="Infinigen",
                data=glb,
                ext="glb",
                title=asset_id,
                meta={"depiction": "whole_plant", "factory": factory, "render": "mesh"},
            )
            out.source = "infinigen"
            out.license = INFINIGEN_LICENSE
            out.attribution = f"Infinigen procedural ({factory})"
            out.external_url = INFINIGEN_URL
            db.commit()  # provenance committed → hosted
            report["hosted"] += 1
            report["by_factory"][factory] = report["by_factory"].get(factory, 0) + 1
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
            # Guard the rollback so a secondary failure here can't abort the remaining batch.
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
    from app.mesh_convert import to_glb as _to_glb

    ap = argparse.ArgumentParser(description=__doc__)
    # Infinigen 1.19.1 plant factories that EXPORT REAL GEOMETRY via --export obj (verified):
    # Succulent, SnakePlant (~1.3M faces, decimated downstream). NOTE: Flowerplant/Fern are
    # instanced/scatter assets whose --export obj is EMPTY (geometry only in instances); they
    # would need a Blender realize+decimate path, so they are not the default.
    ap.add_argument("--factory", default="Succulent")
    ap.add_argument("-n", type=int, default=3)
    ap.add_argument(
        "--env-python",
        default=os.environ.get("INFINIGEN_PYTHON", ""),
        help="path to the infinigen conda env python (or set INFINIGEN_PYTHON)",
    )
    ap.add_argument("--max-faces", type=int, default=150_000)
    ap.add_argument("--no-score", action="store_true")
    args = ap.parse_args()

    env_python = args.env_python or "python"
    out_dir = tempfile.mkdtemp(prefix="infinigen_")
    # Verified against `generate_individual_assets --help` (infinigen 1.19.1): -f/--factories,
    # -n/--n_images, -r/--render {none}, --export {obj}. Headless geometry-only invocation.
    cmd = [
        env_python,
        "-m",
        "infinigen_examples.generate_individual_assets",
        "--output_folder",
        out_dir,
        "-f",
        args.factory,
        "-n",
        str(args.n),
        "--render",
        "none",
        "--export",
        "obj",
    ]
    print("running:", " ".join(cmd))
    try:
        subprocess.run(cmd, check=True, timeout=1800)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(
            f"infinigen generation failed: {e} — is the infinigen env installed? "
            f"(set --env-python or INFINIGEN_PYTHON to its conda-env python)"
        )
        return 1
    objs = sorted(str(p) for p in Path(out_dir).rglob("*.obj"))
    if not objs:
        print(f"no .obj produced under {out_dir}")
        return 1

    max_faces = args.max_faces or None

    def to_glb(path: str) -> bytes:
        return _to_glb(path, max_faces=max_faces)

    db = SessionLocal()
    try:
        report = ingest_infinigen(
            db,
            objs,
            to_glb=to_glb,
            score_fn=None if args.no_score else recon_service.score_and_store,
            factory=args.factory,
        )
        print(report)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
