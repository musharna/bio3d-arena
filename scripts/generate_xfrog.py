"""Ingest XfrogPlants botanical tomato growth-stage models as `found:xfrog` entries.

XfrogPlants are PURCHASED commercial botanical models (NOT procedural generations we drive) — the
highest-fidelity, photoreal corner of the field, ingested as a growth-stage phenology series. The
FBX source files are licensed assets that are NOT committed to the repo; `main()` reads them from a
configured XfrogPlants Agriculture directory, converts FBX→GLB in Blender (preserving textures +
alpha-cutout foliage) with a decimate pass to keep them gallery-weight, then ingests. License is
commercial — flagged for the pre-public `/spotlight` license re-vet.

`ingest_xfrog` is the testable core (GLB bytes + scorer injected). The tomato is AG15
(Solanum lycopersicum); stages span seedling → fruiting bush.
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
XFROG_LICENSE = "XfrogPlants commercial (purchased) — internal use; re-vet before public display"
# A curated growth-stage arc (the AG15 tomato ships 10 stages, AG15_1..AG15_10).
TOMATO_STAGES = [2, 5, 8, 10]


def ingest_xfrog(db, items, *, to_glb, score_fn=None, task_title=TOMATO_TITLE, limit=20) -> dict:
    """items: iterable of (glb_path, stage_int). Hosts each as source='found:xfrog'."""
    task = db.execute(select(Task).where(Task.title == task_title)).scalars().first()
    if task is None:
        raise RuntimeError(f"subject task not found: {task_title!r}")
    report = {"hosted": 0, "skipped": 0, "errors": 0, "by_variant": {}}
    for path, stage in list(items)[:limit]:
        variant = f"xfrog-AG15-s{stage}"
        try:
            try:
                glb = to_glb(path)
            except MeshConvertError as e:
                print(f"  skip {variant}: {e}")
                report["skipped"] += 1
                continue
            out, _created = ingest.register_output(
                db,
                task_id=task.id,
                generator_slug=variant,
                generator_name="XfrogPlants (botanical)",
                data=glb,
                ext="glb",
                title=f"XfrogPlants tomato — growth stage {stage}",
                meta={
                    "depiction": "whole_plant",
                    "variant": variant,
                    "render": "mesh",
                    "growth_stage": stage,
                },
            )
            out.source = "found:xfrog"
            out.license = XFROG_LICENSE
            out.attribution = (
                f"XfrogPlants Agriculture — Solanum lycopersicum (AG15), growth stage {stage} "
                "— purchased botanical model (Xfrog)"
            )
            out.external_url = "https://www.xfrog.net/product-page/library-agriculture"
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
            print(f"  error {variant}: {e}")
            report["errors"] += 1
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                pass
    return report


# FBX→GLB: import (textures resolve from the FBX's dir), CLIP alpha on cutout foliage, decimate to
# a target face budget so the high-poly botanical model is gallery-weight, export GLB.
_FBX_CONVERT = r"""
import bpy, sys, json
a = json.loads(sys.argv[sys.argv.index("--") + 1])
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.fbx(filepath=a["src"])
for m in bpy.data.materials:
    if not m.use_nodes:
        continue
    bsdf = m.node_tree.nodes.get("Principled BSDF")
    if bsdf and bsdf.inputs["Alpha"].is_linked:
        m.blend_method = "CLIP"
        m.alpha_threshold = 0.5
budget = a.get("max_faces", 45000)
total = sum(len(o.data.polygons) for o in bpy.data.objects if o.type == "MESH")
if total > budget:
    ratio = max(0.05, budget / total)
    for o in bpy.data.objects:
        if o.type == "MESH":
            d = o.modifiers.new("dec", "DECIMATE")
            d.ratio = ratio
bpy.ops.export_scene.gltf(filepath=a["out"], export_format="GLB", use_selection=False,
                          export_apply=True)
"""


def main() -> int:
    import argparse
    import json
    import os
    import subprocess
    import tempfile

    from app import recon_service
    from app.database import SessionLocal

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--xfrog-dir",
        default=os.environ.get("XFROG_AGRICULTURE_DIR", ""),
        help="path to the extracted 'XfrogPlants_Agriculture' FBX dir (or set XFROG_AGRICULTURE_DIR)",
    )
    ap.add_argument(
        "--blender", default=os.environ.get("BLENDER_BIN", str(Path.home() / "blender/blender"))
    )
    ap.add_argument("--max-faces", type=int, default=45000)
    ap.add_argument("--no-score", action="store_true")
    args = ap.parse_args()

    if not args.xfrog_dir or not Path(args.xfrog_dir).exists():
        print(
            "set XFROG_AGRICULTURE_DIR (or --xfrog-dir) to the extracted XfrogPlants_Agriculture dir"
        )
        return 1
    if not Path(args.blender).exists():
        print(f"Blender binary not found: {args.blender}")
        return 1
    tomato_dir = Path(args.xfrog_dir) / "AG15_Solanum_lycopersicum_Tomato"
    if not tomato_dir.exists():
        print(f"tomato model dir not found: {tomato_dir}")
        return 1

    work = Path(tempfile.mkdtemp(prefix="xfrog_"))
    script = work / "fbx2glb.py"
    script.write_text(_FBX_CONVERT)
    items = []
    for stage in TOMATO_STAGES:
        fbx = tomato_dir / f"AG15_{stage}.FBX"
        if not fbx.exists():
            print(f"  stage {stage}: {fbx.name} not found")
            continue
        glb = work / f"AG15_{stage}.glb"
        try:
            subprocess.run(
                [
                    args.blender,
                    "-b",
                    "-P",
                    str(script),
                    "--",
                    json.dumps({"src": str(fbx), "out": str(glb), "max_faces": args.max_faces}),
                ],
                check=True,
                timeout=600,
            )
            if glb.exists():
                items.append((str(glb), stage))
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
            print(f"  stage {stage} convert failed: {e}")
    if not items:
        print("no Xfrog tomato stages converted")
        return 1

    db = SessionLocal()
    try:
        report = ingest_xfrog(
            db,
            items,
            to_glb=lambda p: Path(p).read_bytes(),
            score_fn=None if args.no_score else recon_service.score_and_store,
        )
        print(report)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
