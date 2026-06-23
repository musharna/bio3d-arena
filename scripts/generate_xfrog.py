"""Ingest XfrogPlants botanical crop growth-stage models as `found:xfrog` entries.

XfrogPlants are PURCHASED commercial botanical models (NOT procedural generations we drive) — the
highest-fidelity, photoreal corner of the field, ingested as a growth-stage phenology series. The
FBX source files are licensed assets that are NOT committed to the repo; `main()` reads them from a
configured XfrogPlants Agriculture directory, converts FBX→GLB in Blender (preserving textures +
alpha-cutout foliage) with a decimate pass to keep them gallery-weight, then ingests. License is
commercial — flagged for the pre-public `/spotlight` license re-vet.

`ingest_xfrog` is the testable core (GLB bytes + scorer injected). The library spans 20 crops
(AG01..AG20); `CROPS` maps each spotlight crop to its AG code, species, model directory, subject
task, and curated growth-stage arc. Tomato = AG15 (Solanum lycopersicum), maize = AG20 (Zea mays).
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
XFROG_LICENSE = "XfrogPlants commercial (purchased) — internal use; re-vet before public display"
# A curated growth-stage arc (the AG15 tomato ships 10 stages, AG15_1..AG15_10).
TOMATO_STAGES = [2, 5, 8, 10]

# Per-crop config: AG code, species, common name, model subdir in the XfrogPlants_Agriculture
# library, the spotlight subject task title, and a curated growth-stage arc (hero last).
CROPS = {
    "tomato": {
        "ag_code": "AG15",
        "species": "Solanum lycopersicum",
        "common": "tomato",
        "model_dir": "AG15_Solanum_lycopersicum_Tomato",
        "task_title": TOMATO_TITLE,
        "stages": TOMATO_STAGES,
    },
    "maize": {
        "ag_code": "AG20",
        "species": "Zea mays",
        "common": "maize (corn)",
        "model_dir": "AG20_Zea_mays_Corn",
        "task_title": MAIZE_TITLE,
        # AG20 ships 9 stages (AG20_1..AG20_9); 8 = fruiting hero (visible ears), 3/5/7 = phenology.
        "stages": [3, 5, 7, 8],
    },
}


def ingest_xfrog(
    db,
    items,
    *,
    to_glb,
    score_fn=None,
    task_title=TOMATO_TITLE,
    ag_code="AG15",
    species="Solanum lycopersicum",
    common="tomato",
    limit=20,
) -> dict:
    """items: iterable of (glb_path, stage_int). Hosts each as source='found:xfrog'.

    Crop identity (ag_code/species/common) drives the variant slug, output title, and attribution;
    defaults preserve the original AG15 tomato behaviour.
    """
    task = db.execute(select(Task).where(Task.title == task_title)).scalars().first()
    if task is None:
        raise RuntimeError(f"subject task not found: {task_title!r}")
    report = {"hosted": 0, "skipped": 0, "errors": 0, "by_variant": {}}
    for path, stage in list(items)[:limit]:
        variant = f"xfrog-{ag_code}-s{stage}"
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
                title=f"XfrogPlants {common} — growth stage {stage}",
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
                f"XfrogPlants Agriculture — {species} ({ag_code}), growth stage {stage} "
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
# Alpha wiring: some XfrogPlants crops (e.g. AG20 corn) ship the foliage alpha as a sibling
# `<basename>_a.tif` that the FBX does NOT link to the Principled Alpha input — without wiring it,
# leaves render as opaque rectangles. We load that sibling and connect it before CLIPping.
_FBX_CONVERT = r"""
import bpy, sys, json, os
a = json.loads(sys.argv[sys.argv.index("--") + 1])
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.fbx(filepath=a["src"])
srcdir = os.path.dirname(a["src"])

def _basecolor_tex(mat, bsdf):
    for ln in mat.node_tree.links:
        if ln.to_node == bsdf and ln.to_socket.name == "Base Color" and ln.from_node.type == "TEX_IMAGE":
            return ln.from_node
    return None

for m in bpy.data.materials:
    if not m.use_nodes:
        continue
    bsdf = m.node_tree.nodes.get("Principled BSDF")
    if not bsdf:
        continue
    if bsdf.inputs["Alpha"].is_linked:
        m.blend_method = "CLIP"
        m.alpha_threshold = 0.5
        continue
    basetex = _basecolor_tex(m, bsdf)
    if not (basetex and basetex.image):
        continue
    stem = os.path.splitext(os.path.basename(basetex.image.filepath_raw or basetex.image.name))[0]
    for cand in (stem + "_a.tif", stem + "_a.TIF"):
        ap = os.path.join(srcdir, cand)
        if os.path.exists(ap):
            anode = m.node_tree.nodes.new("ShaderNodeTexImage")
            anode.image = bpy.data.images.load(ap, check_existing=True)
            anode.image.colorspace_settings.name = "Non-Color"
            for ln in m.node_tree.links:
                if ln.to_node == basetex and ln.to_socket.name == "Vector":
                    m.node_tree.links.new(ln.from_socket, anode.inputs["Vector"])
            m.node_tree.links.new(anode.outputs["Color"], bsdf.inputs["Alpha"])
            m.blend_method = "CLIP"
            m.alpha_threshold = 0.5
            break
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
    ap.add_argument(
        "--crop",
        default="tomato",
        choices=sorted(CROPS),
        help="which XfrogPlants crop to ingest (default: tomato)",
    )
    ap.add_argument(
        "--stages",
        default="",
        help="comma-separated stage override (default: the crop's curated arc)",
    )
    args = ap.parse_args()

    crop = CROPS[args.crop]
    ag_code = crop["ag_code"]
    stages = [int(s) for s in args.stages.split(",") if s.strip()] or crop["stages"]

    if not args.xfrog_dir or not Path(args.xfrog_dir).exists():
        print(
            "set XFROG_AGRICULTURE_DIR (or --xfrog-dir) to the extracted XfrogPlants_Agriculture dir"
        )
        return 1
    if not Path(args.blender).exists():
        print(f"Blender binary not found: {args.blender}")
        return 1
    model_dir = Path(args.xfrog_dir) / crop["model_dir"]
    if not model_dir.exists():
        print(f"{args.crop} model dir not found: {model_dir}")
        return 1

    work = Path(tempfile.mkdtemp(prefix="xfrog_"))
    script = work / "fbx2glb.py"
    script.write_text(_FBX_CONVERT)
    items = []
    for stage in stages:
        fbx = model_dir / f"{ag_code}_{stage}.FBX"
        if not fbx.exists():
            print(f"  stage {stage}: {fbx.name} not found")
            continue
        glb = work / f"{ag_code}_{stage}.glb"
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
        print(f"no Xfrog {args.crop} stages converted")
        return 1

    db = SessionLocal()
    try:
        report = ingest_xfrog(
            db,
            items,
            to_glb=lambda p: Path(p).read_bytes(),
            score_fn=None if args.no_score else recon_service.score_and_store,
            task_title=crop["task_title"],
            ag_code=ag_code,
            species=crop["species"],
            common=crop["common"],
        )
        print(report)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
