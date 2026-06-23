"""Generate a Demeter learned-morphable plant and ingest it as a 'procedural:demeter' entry.

Demeter is a learned (PCA-morphable) plant generator: per-organ shape bases decode a fixed-topology
plant graph into a triangle mesh. `ingest_demeter` is the testable core (GLB bytes + scorer
injected); `main()` subprocesses Demeter's own env to run `demeter_runner.py` (Demeter is consumed
READ-ONLY, never imported into this venv), converts the OBJ to an upright GLB, then ingests.

Demeter is Academic-Non-Commercial — entries are internal-only, flagged for the pre-public
`/spotlight` license re-vet (like the Xfrog assets). Maize verdict (gated 2026-06-22): a recognizable
young plant with broad strap leaves, but clumpy uncolored PCA surfaces and no tassel/ear — caveat-class.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app import ingest  # noqa: E402
from app.mesh_convert import MeshConvertError  # noqa: E402
from app.models import Task  # noqa: E402

MAIZE_TITLE = "Zea mays — single-image → 3D reconstruction"
ROSE_TITLE = "Rosa — single-image → 3D reconstruction"
# Map a Demeter --species to the bio3d-arena subject it attaches to.
SPECIES_TASKS = {"maize": MAIZE_TITLE, "rose": ROSE_TITLE}
DEMETER_LICENSE = (
    "Demeter (academic non-commercial research generator — internal use; re-vet before public)"
)
DEMETER_ATTRIBUTION = "Demeter learned-morphable plant generator (learned PCA plant graph)"
DEMETER_CAVEAT = "learned PCA-morphable mesh — clumpy uncolored leaves, no tassel/ear, low fidelity"


def ingest_demeter(
    db,
    obj_or_glb_paths,
    *,
    to_glb,
    score_fn=None,
    variant="maize",
    task_title=MAIZE_TITLE,
    attribution=DEMETER_ATTRIBUTION,
    caveat=DEMETER_CAVEAT,
    limit=10,
) -> dict:
    """paths: iterable of source mesh paths. `to_glb(path)` returns GLB bytes. Hosts each as
    source='procedural:demeter' on the subject task. Defaults target the maize subject."""
    task = db.execute(select(Task).where(Task.title == task_title)).scalars().first()
    if task is None:
        raise RuntimeError(f"subject task not found: {task_title!r}")
    report = {"hosted": 0, "skipped": 0, "errors": 0, "by_variant": {}}
    for path in list(obj_or_glb_paths)[:limit]:
        asset_id = Path(path).stem
        try:
            try:
                glb = to_glb(path)
            except MeshConvertError as e:
                print(f"  skip {asset_id}: {e}")
                report["skipped"] += 1
                continue
            meta = {"depiction": "whole_plant", "variant": variant, "render": "mesh"}
            if caveat:
                meta["caveat"] = caveat
            out, _created = ingest.register_output(
                db,
                task_id=task.id,
                generator_slug="demeter",
                generator_name="Demeter",
                data=glb,
                ext="glb",
                title=asset_id,
                meta=meta,
            )
            out.source = "procedural:demeter"
            out.license = DEMETER_LICENSE
            out.attribution = f"{attribution} [{variant}]"
            out.external_url = None  # internal generator: hosted locally, no external link
            db.commit()
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


def obj_to_glb_yup(obj_path: str) -> bytes:
    """Load a Demeter OBJ (Z-up) and return GLB bytes oriented Y-up (recentred) for <model-viewer>.

    Demeter aligns the main stem to +Z; glTF/<model-viewer> is +Y up, so rotate −90° about X
    (Z→Y) and recentre on the origin. Then face the broad leaf spread toward the default
    <model-viewer> camera: a distichous (two-ranked) plant is thin edge-on, so if the larger
    horizontal extent lies along depth (Z), yaw 90° about the up-axis (Y) to present it.
    Raises MeshConvertError on an unreadable/empty mesh.
    """
    import numpy as np
    import trimesh

    try:
        m = trimesh.load(obj_path, force="mesh")
    except Exception as e:  # noqa: BLE001
        raise MeshConvertError(f"trimesh load failed: {e}") from e
    if m is None or getattr(m, "is_empty", True) or len(m.vertices) == 0:
        raise MeshConvertError("empty mesh")
    m.apply_transform(trimesh.transformations.rotation_matrix(-np.pi / 2, [1, 0, 0]))
    ex = m.extents  # (X, Y_up, Z_depth)
    if ex[2] > ex[0]:  # broad spread is along depth → yaw so it faces the camera
        m.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [0, 1, 0]))
    m.apply_translation(-m.centroid)
    return m.export(file_type="glb")


def main() -> int:
    import argparse
    import os
    import subprocess
    import tempfile

    from app import recon_service
    from app.database import SessionLocal

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--demeter-dir",
        default=os.environ.get("DEMETER_DIR", str(Path.home() / "Demeter")),
        help="path to the Demeter checkout (or set DEMETER_DIR)",
    )
    ap.add_argument(
        "--env-python",
        default=os.environ.get(
            "DEMETER_PYTHON", str(Path.home() / "miniconda3/envs/partcrafter/bin/python")
        ),
        help="python interpreter for Demeter's env (CUDA + open3d); or set DEMETER_PYTHON",
    )
    ap.add_argument("--species", default="maize")
    ap.add_argument(
        "--samples",
        default="10008da",
        help="comma-separated Demeter sample instance names",
    )
    ap.add_argument("--no-score", action="store_true")
    ap.add_argument(
        "--caveat",
        default=None,
        help="caveat badge text (default: the Demeter caveat; empty string to omit)",
    )
    args = ap.parse_args()

    caveat = DEMETER_CAVEAT if args.caveat is None else args.caveat
    runner = Path(__file__).resolve().parent / "demeter_runner.py"
    env_py = Path(args.env_python)
    if not env_py.exists():
        print(f"Demeter env python not found: {env_py}")
        return 1
    if not (Path(args.demeter_dir) / "decode.py").exists():
        print(f"Demeter checkout not found at: {args.demeter_dir}")
        return 1

    out_dir = tempfile.mkdtemp(prefix="demeter_")
    objs = []
    for sample in [s.strip() for s in args.samples.split(",") if s.strip()]:
        obj = str(Path(out_dir) / f"demeter_{args.species}_{sample}.obj")
        try:
            subprocess.run(
                [str(env_py), str(runner), args.demeter_dir, args.species, sample, obj],
                check=True,
                timeout=600,
            )
            if Path(obj).exists():
                objs.append(obj)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
            print(f"  demeter sample {sample} failed: {e}")
    if not objs:
        print(f"no Demeter mesh produced under {out_dir}")
        return 1

    db = SessionLocal()
    try:
        report = ingest_demeter(
            db,
            objs,
            to_glb=obj_to_glb_yup,
            score_fn=None if args.no_score else recon_service.score_and_store,
            variant=args.species,
            task_title=SPECIES_TASKS.get(args.species, MAIZE_TITLE),
            caveat=caveat or None,
        )
        print(report)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
