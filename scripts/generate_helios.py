"""Generate procedural tomato plants with Helios (UC Davis) and ingest them as a
'procedural:helios' entry. `ingest_helios` is the testable core (OBJ->GLB + scorer injected);
`main()` runs the built ~/Helios/projects/tomato_gen binary, then collects + ingests. Commits
per object. Helios is a separate C++ build (NOT pip's unrelated `pyhelios` CFD package), invoked
by subprocess.
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
HELIOS_LICENSE = "GPL-2.0 (Helios, UC Davis Bailey Lab)"
HELIOS_URL = "https://github.com/PlantSimulationLab/Helios"


def ingest_helios(
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
                generator_slug="helios",
                generator_name="Helios",
                data=glb,
                ext="glb",
                title=asset_id,
                meta={"depiction": "whole_plant", "variant": variant, "render": "mesh"},
            )
            out.source = "procedural:helios"
            out.license = HELIOS_LICENSE
            out.attribution = f"Helios procedural {variant} (CanopyGenerator)"
            out.external_url = HELIOS_URL
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
    from app.mesh_convert import to_glb as _to_glb

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--bin",
        default=os.environ.get(
            "HELIOS_TOMATO_BIN", str(Path.home() / "Helios/projects/tomato_gen/build/tomato_gen")
        ),
        help="path to the built Helios tomato_gen binary (or set HELIOS_TOMATO_BIN)",
    )
    ap.add_argument("-n", type=int, default=3, help="number of seeds/plants to generate")
    ap.add_argument("--max-faces", type=int, default=150_000)
    ap.add_argument("--no-score", action="store_true")
    args = ap.parse_args()

    if not Path(args.bin).exists():
        print(f"Helios tomato_gen binary not found: {args.bin} — build the project first")
        return 1
    out_dir = tempfile.mkdtemp(prefix="helios_")
    objs = []
    for seed in range(args.n):
        obj = str(Path(out_dir) / f"tomato_{seed}.obj")
        # tomato_gen writes an OBJ at argv[1] for the given seed (argv[2]); verify the binary's
        # arg contract against its main.cpp at build time.
        try:
            subprocess.run([args.bin, obj, str(seed)], check=True, timeout=600)
            if Path(obj).exists():
                objs.append(obj)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
            print(f"  helios seed {seed} failed: {e}")
    if not objs:
        print(f"no .obj produced under {out_dir}")
        return 1

    max_faces = args.max_faces or None

    def to_glb(path: str) -> bytes:
        return _to_glb(path, max_faces=max_faces)

    db = SessionLocal()
    try:
        report = ingest_helios(
            db,
            objs,
            to_glb=to_glb,
            score_fn=None if args.no_score else recon_service.score_and_store,
        )
        print(report)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
