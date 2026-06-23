"""Generate a tomato 3D model with PartCrafter (NeurIPS'25, part-based image→3D) from the reference
photo and ingest it as a `frontier:partcrafter` entry (AI class). PartCrafter is a self-hosted neural
part-based generator — the frontier-generative baseline the procedural path is measured against.

Build-gate finding (2026-06-22): PartCrafter runs cleanly on the RTX 5090 (MIT code+weights, ~55s)
but BLOBS the tomato plant — the "parts" are amorphous chunks, not stem/leaf/fruit organs (thin
foliage collapses in mesh-diffusion). Ingested as the honest generative baseline, not a faithful
tomato. `ingest_partcrafter` is the testable core; `main()` runs the self-hosted inference (its own
conda env, via subprocess), decimates the high-poly result, then ingests.
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
PARTCRAFTER_LICENSE = "PartCrafter MIT (code+weights); generated from the CC reference photo"
PARTCRAFTER_URL = "https://github.com/wgsxm/PartCrafter"

# Per-crop config: subject task + default CC reference photo + inference tag (also the variant
# slug + output subdir). PartCrafter runs the same self-hosted pipeline per crop; only the photo
# changes. Defaults below preserve the original tomato behaviour.
CROPS = {
    "tomato": {
        "task_title": TOMATO_TITLE,
        "image": "data/assets/reference/tomato_ref.jpg",
        "tag": "tomato",
    },
    "maize": {
        "task_title": MAIZE_TITLE,
        "image": "data/assets/reference/maize_ref.jpg",
        "tag": "maize",
    },
}


def ingest_partcrafter(
    db, items, *, to_glb, score_fn=None, variant="tomato", task_title=TOMATO_TITLE
) -> dict:
    """items: iterable of (glb_path, n_parts). Hosts each as source='frontier:partcrafter'."""
    task = db.execute(select(Task).where(Task.title == task_title)).scalars().first()
    if task is None:
        raise RuntimeError(f"subject task not found: {task_title!r}")
    report = {"hosted": 0, "skipped": 0, "errors": 0, "by_variant": {}}
    for path, n_parts in list(items):
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
                generator_slug="partcrafter",
                generator_name="PartCrafter (part-based)",
                data=glb,
                ext="glb",
                title=f"PartCrafter recon ({n_parts} parts)",
                meta={
                    "depiction": "whole_plant",
                    "variant": variant,
                    "render": "mesh",
                    "modality": "part-based",
                    "n_parts": n_parts,
                    "self_hosted": True,
                },
            )
            out.source = "frontier:partcrafter"
            out.license = PARTCRAFTER_LICENSE
            out.attribution = (
                f"PartCrafter part-based image→3D ({n_parts} parts) — self-hosted neural frontier "
                "(generative baseline; blobs thin foliage)"
            )
            out.external_url = PARTCRAFTER_URL
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
        except Exception as e:  # noqa: BLE001 — one asset never aborts the batch
            print(f"  error {variant}: {e}")
            report["errors"] += 1
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                pass
    return report


# Decimate the high-poly PartCrafter GLB to gallery weight (preserves per-part materials).
_DECIMATE = r"""
import bpy, sys, json
a = json.loads(sys.argv[sys.argv.index("--") + 1])
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=a["src"])
budget = a.get("max_faces", 60000)
total = sum(len(o.data.polygons) for o in bpy.data.objects if o.type == "MESH")
if total > budget:
    ratio = max(0.02, budget / total)
    for o in bpy.data.objects:
        if o.type == "MESH":
            o.modifiers.new("dec", "DECIMATE").ratio = ratio
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

    repo = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--pc-dir", default=os.environ.get("PARTCRAFTER_DIR", str(Path.home() / "PartCrafter"))
    )
    ap.add_argument(
        "--pc-python",
        default=os.environ.get(
            "PARTCRAFTER_PYTHON", str(Path.home() / "miniconda3/envs/partcrafter/bin/python")
        ),
    )
    ap.add_argument(
        "--blender", default=os.environ.get("BLENDER_BIN", str(Path.home() / "blender/blender"))
    )
    ap.add_argument(
        "--crop", default="tomato", choices=sorted(CROPS), help="which crop to reconstruct"
    )
    ap.add_argument(
        "--image", default="", help="reference photo (default: the crop's CC reference photo)"
    )
    ap.add_argument("--num-parts", type=int, default=3)
    ap.add_argument("--no-score", action="store_true")
    args = ap.parse_args()

    crop = CROPS[args.crop]
    tag = crop["tag"]
    if not args.image:
        args.image = str(repo / crop["image"])

    pc_dir, pc_py, blender = Path(args.pc_dir), Path(args.pc_python), Path(args.blender)
    if not pc_py.exists():
        print(f"PartCrafter env python not found: {pc_py} — set PARTCRAFTER_PYTHON")
        return 1
    if not Path(args.image).exists():
        print(f"reference image missing: {args.image}")
        return 1

    work = Path(tempfile.mkdtemp(prefix="partcrafter_"))
    env = {**os.environ, "PYTHONPATH": str(pc_dir), "CUDA_HOME": str(pc_py.parent.parent)}
    try:
        subprocess.run(
            [
                str(pc_py),
                "scripts/inference_partcrafter.py",
                "--image_path",
                args.image,
                "--num_parts",
                str(args.num_parts),
                "--tag",
                tag,
                "--rmbg",
                "--output_dir",
                str(work),
            ],
            cwd=str(pc_dir),
            check=True,
            timeout=1200,
            env=env,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(f"  PartCrafter inference failed: {e}")
        return 1
    raw = work / tag / "object.glb"
    if not raw.exists():
        print(f"no object.glb produced at {raw}")
        return 1

    glb = work / f"{tag}_decimated.glb"
    if blender.exists():
        script = work / "dec.py"
        script.write_text(_DECIMATE)
        try:
            subprocess.run(
                [
                    str(blender),
                    "-b",
                    "-P",
                    str(script),
                    "--",
                    json.dumps({"src": str(raw), "out": str(glb), "max_faces": 60000}),
                ],
                check=True,
                timeout=300,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
            print(f"  Blender decimate failed: {e}")
            return 1
    else:
        print(f"  Blender not found at {blender} — ingesting full-res")
        glb = raw  # no Blender → ingest full-res

    db = SessionLocal()
    try:
        report = ingest_partcrafter(
            db,
            [(str(glb), args.num_parts)],
            to_glb=lambda p: Path(p).read_bytes(),
            score_fn=None if args.no_score else recon_service.score_and_store,
            variant=args.crop,
            task_title=crop["task_title"],
        )
        print(report)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
