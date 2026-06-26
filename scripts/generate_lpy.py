"""Generate a procedural tomato from an authored L-Py / PlantGL L-system and ingest it as a
'procedural:lpy' entry. `ingest_lpy` is the testable core (GLB bytes + scorer injected); `main()`
subprocesses the `lpy` conda env to run `lpy_runner.py` on `lpy/tomato.lpy` (PlantGL lives only in
that env), converts the OBJ to a Y-up double-sided GLB, then ingests. Commits per object.

Unlike Helios/AgriGen, the L-Py tomato cleared the independent critic gate (pinnately compound
serrated leaves + red fruit trusses + upright habit), so it carries NO low-fidelity caveat.
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
MAIZE_TITLE = "Zea mays — single-image → 3D reconstruction"
SOYBEAN_TITLE = "Glycine max — single-image → 3D reconstruction"
ARABIDOPSIS_TITLE = "Arabidopsis thaliana — single-image → 3D reconstruction"
PINE_TITLE = "Pinus sylvestris — single-image → 3D reconstruction"
LPY_LICENSE = "L-Py/PlantGL (OpenAlea, CeCILL-C); L-system authored for bio3d-arena"
LPY_URL = "https://github.com/openalea/lpy"

# Per-crop authored L-system. Track B LLM-synth: an LLM authors a per-crop .lpy → critic-gate → ingest.
CROPS = {
    "tomato": {"model": "lpy/tomato.lpy", "task_title": TOMATO_TITLE, "variant": "tomato"},
    "maize": {"model": "lpy/maize.lpy", "task_title": MAIZE_TITLE, "variant": "maize"},
    "soybean": {"model": "lpy/soybean.lpy", "task_title": SOYBEAN_TITLE, "variant": "soybean"},
    "arabidopsis": {
        "model": "lpy/arabidopsis.lpy",
        "task_title": ARABIDOPSIS_TITLE,
        "variant": "arabidopsis",
    },
    "pinus": {"model": "lpy/pine.lpy", "task_title": PINE_TITLE, "variant": "pinus"},
}


def ingest_lpy(
    db, obj_paths, *, to_glb, score_fn=None, variant="tomato", task_title=TOMATO_TITLE, limit=10
) -> dict:
    task = db.execute(select(Task).where(Task.title == task_title)).scalars().first()
    if task is None:
        raise RuntimeError(f"subject task not found: {task_title!r}")
    report = {"hosted": 0, "skipped": 0, "errors": 0, "by_variant": {}}
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
                generator_slug="lpy",
                generator_name="L-Py",
                data=glb,
                ext="glb",
                title=asset_id,
                meta={"depiction": "whole_plant", "variant": variant, "render": "mesh"},
            )
            out.source = "procedural:lpy"
            out.license = LPY_LICENSE
            out.attribution = f"Authored L-Py/PlantGL {variant} L-system"
            out.external_url = LPY_URL
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
    from scripts.lpy_glb import obj_to_glb

    repo = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--lpy-python",
        default=os.environ.get(
            "LPY_ENV_PYTHON", str(Path.home() / "miniconda3/envs/lpy/bin/python")
        ),
        help="python of the conda env with openalea.lpy + plantgl (or set LPY_ENV_PYTHON)",
    )
    ap.add_argument(
        "--crop", default="tomato", choices=sorted(CROPS), help="which crop's authored L-system"
    )
    ap.add_argument("--model", default="", help="override .lpy path (default: the crop's model)")
    ap.add_argument("--no-score", action="store_true")
    args = ap.parse_args()

    crop = CROPS[args.crop]
    if not args.model:
        args.model = str(repo / crop["model"])

    lpy_py = Path(args.lpy_python)
    runner = repo / "scripts/lpy_runner.py"
    if not lpy_py.exists():
        print(f"L-Py env python not found: {lpy_py} — create the conda 'lpy' env first")
        return 1
    if not Path(args.model).exists():
        print(f"L-Py model not found: {args.model}")
        return 1

    out_dir = tempfile.mkdtemp(prefix="lpy_")
    # name the OBJ per crop so the ingested output's title is the crop, not a stray "tomato"
    obj = str(Path(out_dir) / f"{args.crop}.obj")
    try:
        subprocess.run([str(lpy_py), str(runner), args.model, obj], check=True, timeout=300)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(f"  lpy run failed: {e}")
        return 1
    if not Path(obj).exists():
        print(f"no .obj produced at {obj}")
        return 1

    db = SessionLocal()
    try:
        report = ingest_lpy(
            db,
            [obj],
            to_glb=obj_to_glb,
            score_fn=None if args.no_score else recon_service.score_and_store,
            variant=crop["variant"],
            task_title=crop["task_title"],
        )
        print(report)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
