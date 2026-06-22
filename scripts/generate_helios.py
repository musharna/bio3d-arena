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
# Helios is a radiation-simulation FSPM: its tomato is botanically parameterized but exports as
# billboard leaf-tiles on thin shoots, so a static GLB reads as scattered foliage rather than a
# fully coherent plant. We ingest it for honest whole-field coverage with this caveat surfaced.
HELIOS_CAVEAT = "FSPM sim mesh — low standalone fidelity"


def ingest_helios(
    db,
    obj_paths,
    *,
    to_glb,
    score_fn=None,
    variant="tomato",
    task_title=TOMATO_TITLE,
    limit=10,
    caveat=None,
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
            meta = {"depiction": "whole_plant", "variant": variant, "render": "mesh"}
            if caveat:
                meta["caveat"] = caveat
            out, _created = ingest.register_output(
                db,
                task_id=task.id,
                generator_slug="helios",
                generator_name="Helios",
                data=glb,
                ext="glb",
                title=asset_id,
                meta=meta,
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
    ap.add_argument("--caveat", default=HELIOS_CAVEAT, help="caveat badge text (empty to omit)")
    ap.add_argument(
        "--no-texture",
        action="store_true",
        help="skip the Blender alpha-cutout leaf texture (flat untextured leaves)",
    )
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

    def mesh_to_glb(path: str) -> bytes:
        return _to_glb(path, max_faces=max_faces)

    # Prefer the Blender pass that restores the alpha-cutout leaf texture Helios drops on export;
    # fall back to a plain mesh convert (flat untextured leaves) when Blender is unavailable.
    to_glb = mesh_to_glb
    if not args.no_texture:
        try:
            from scripts.helios_glb import BlenderUnavailable, to_textured_glb

            def _probe():
                from pathlib import Path as _P

                from scripts.helios_glb import DEFAULT_BLENDER, DEFAULT_LEAF_TEX
                return _P(DEFAULT_BLENDER).exists() and _P(DEFAULT_LEAF_TEX).exists()

            if _probe():
                to_glb = to_textured_glb
            else:
                print("  Blender/leaf-texture unavailable — leaves will be flat/untextured")
        except BlenderUnavailable as e:
            print(f"  textured path unavailable ({e}) — flat/untextured leaves")

    db = SessionLocal()
    try:
        report = ingest_helios(
            db,
            objs,
            to_glb=to_glb,
            score_fn=None if args.no_score else recon_service.score_and_store,
            caveat=args.caveat or None,
        )
        print(report)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
