"""Ingest CC-licensed game-ready crop-PLANT assets from Sketchfab as `found:sketchfab` entries.

These are downloaded artist assets, NOT procedural generations — they represent the "game-ready
asset" corner of the field (what a game artist ships), a distinct data point from the procedural
generators. `ingest_sketchfab` is the testable core (GLB bytes + scorer injected); `main()`
downloads each asset via the Sketchfab API (token from ~/.config/sketchfab/token or SKETCHFAB_TOKEN),
converts glTF→GLB via Blender to preserve textures (and isolates the fruiting stage for multi-stage
packs), then ingests. License/attribution recorded per asset; the Sketchfab token is never logged.
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
ROSE_TITLE = "Rosa — single-image → 3D reconstruction"

# Verified on the Sketchfab API (license slug + isDownloadable) 2026-06-22. `keep` isolates the
# fruiting-stage mesh for the multi-stage PolyOne pack (drops the title text + earlier stages).
TOMATO_ASSETS = [
    {
        "variant": "sketchfab-polyone",
        "uid": "7613f2aec8f54695b7c219946473cb24",
        "name": "Free Pack - Stylized Tomato (fruiting stage)",
        "author": "polyone",
        "license": "CC-BY 4.0",
        "keep": ["SM_Tomato_Lv3_Tomato_SG_0", "SM_Tomato_Lv3"],
    },
    {
        "variant": "sketchfab-zvanstone",
        "uid": "e0b559690e384fc0a9f3a05913f609c4",
        "name": "Tomato Plant",
        "author": "zvanstone",
        "license": "CC-BY 4.0",
        "keep": None,
    },
    {
        "variant": "sketchfab-lindaman96",
        "uid": "e3293aa133cb439c96d2d7ca412fdcf5",
        "name": "Tomato Plants (Open Brush)",
        "author": "lindaman96",
        "license": "CC-BY-SA 4.0",
        "keep": None,
    },
]

# Single whole-maize-plant assets (not cobs/kernels/fields/dioramas). Verified on the Sketchfab API
# (license slug 'by'/'by-sa' + isDownloadable + a glTF flavour) 2026-06-22 — all public-safe CC.
MAIZE_ASSETS = [
    {
        "variant": "sketchfab-maize-gilles",
        "uid": "5fd3b104d8104519b061469c365d4974",
        "name": "Maize Corn Plant",
        "author": "gilles.schaeck",
        "license": "CC-BY 4.0",
        "keep": None,
    },
    {
        "variant": "sketchfab-maize-boyce",
        "uid": "773c4a1bd7fc44049b59c177aa162566",
        "name": "corn_plant",
        "author": "boyceojinta",
        "license": "CC-BY 4.0",
        "keep": None,
    },
    {
        "variant": "sketchfab-maize-merp",
        "uid": "421a0d96b7ab4c499fa63e857974e144",
        "name": "Corn Stalk",
        "author": "merpcutemr",
        "license": "CC-BY-SA 4.0",
        "keep": None,
    },
    {
        "variant": "sketchfab-maize-arigura",
        "uid": "a3c4fa7b1b4d4d28b5d480ee7f5c29c6",
        "name": "Corn stalk",
        "author": "arigura",
        "license": "CC-BY 4.0",
        "keep": None,
    },
]

# Whole-rose-plant/bush assets (Rosa). Verified on the Sketchfab API (license slug 'by'/'by-sa' +
# isDownloadable + a glTF flavour) 2026-06-23 — all public-safe CC, gallery-weight (no decimation
# needed). The heavy CC0 botanical whole-plant scans (Rosa multiflora/rugosa, 88-138MB) are held
# back pending a decimate pass on the sketchfab convert.
ROSE_ASSETS = [
    {
        "variant": "sketchfab-rose-overhead",
        "uid": "139cd6e42a3e43bd9117fb44d6091abf",
        "name": "Plant | Rosa chinensis",
        "author": "OVERHEAD",
        "license": "CC-BY 4.0",
        "keep": None,
    },
    {
        "variant": "sketchfab-rose-giantbooley",
        "uid": "a92d6bdc2a364f50b838f62a528fbf40",
        "name": "Rose Bush Scan",
        "author": "giantbooley",
        "license": "CC-BY 4.0",
        "keep": None,
    },
    {
        "variant": "sketchfab-rose-rosticostafi",
        "uid": "3f97dc47b0fd4b1382f169d2f1d3b309",
        "name": "Rose bush",
        "author": "RosticOstafi",
        "license": "CC-BY 4.0",
        "keep": None,
    },
    {
        "variant": "sketchfab-rose-ramakarl",
        "uid": "f4d81ec952d04452a2f8421985875e56",
        "name": "Rose Bush - Detailed",
        "author": "ramakarl",
        "license": "CC-BY 4.0",
        "keep": None,
    },
    # CC0 botanical whole-plant photogrammetry scans (ffishAsia-and-floraZia), full Linnaean
    # taxonomy. HEAVY (1.4-1.5M faces) → ingest with --max-faces (decimate). Cleanest license.
    {
        "variant": "sketchfab-rose-multiflora",
        "uid": "696091d213a44b6680e91c7fa472d26e",
        "name": "Wild Rose (Rosa multiflora)",
        "author": "ffishAsia-and-floraZia",
        "license": "CC0",
        "keep": None,
    },
    {
        "variant": "sketchfab-rose-rugosa",
        "uid": "78127e37fa104f01a8187893574349a8",
        "name": "Hamanasu Rose (Rosa rugosa)",
        "author": "ffishAsia-and-floraZia",
        "license": "CC0",
        "keep": None,
    },
]

# Per-crop: subject task + curated asset set. Defaults preserve the original tomato behaviour.
CROPS = {
    "tomato": {"task_title": TOMATO_TITLE, "assets": TOMATO_ASSETS},
    "maize": {"task_title": MAIZE_TITLE, "assets": MAIZE_ASSETS},
    "rose": {"task_title": ROSE_TITLE, "assets": ROSE_ASSETS},
}


def ingest_sketchfab(
    db, items, *, to_glb, score_fn=None, task_title=TOMATO_TITLE, limit=20
) -> dict:
    """items: iterable of (glb_path, asset_dict). Hosts each as source='found:sketchfab'."""
    task = db.execute(select(Task).where(Task.title == task_title)).scalars().first()
    if task is None:
        raise RuntimeError(f"subject task not found: {task_title!r}")
    report = {"hosted": 0, "skipped": 0, "errors": 0, "by_variant": {}}
    for path, asset in list(items)[:limit]:
        variant = asset["variant"]
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
                generator_name=f"Sketchfab — {asset['author']}",
                data=glb,
                ext="glb",
                title=asset["name"],
                meta={"depiction": "whole_plant", "variant": variant, "render": "mesh"},
            )
            out.source = "found:sketchfab"
            out.license = f"{asset['license']} (Sketchfab, {asset['author']})"
            out.attribution = (
                f"“{asset['name']}” by {asset['author']} — game-ready CC asset (Sketchfab)"
            )
            out.external_url = f"https://sketchfab.com/3d-models/{asset['uid']}"
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


_BLENDER_CONVERT = r"""
import bpy, sys, json
a = json.loads(sys.argv[sys.argv.index("--")+1])
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=a["src"])
keep = a.get("keep")
if keep:
    for o in list(bpy.data.objects):
        if o.type == "MESH" and o.name not in keep:
            bpy.data.objects.remove(o, do_unlink=True)
# Decimate to a gallery face budget for heavy scans (e.g. the 1-2M-face CC0 botanical roses); the
# light game/scan assets stay untouched. Preserves per-part materials; export_apply bakes the modifier.
mf = a.get("max_faces") or 0
if mf:
    total = sum(len(o.data.polygons) for o in bpy.data.objects if o.type == "MESH")
    if total > mf:
        ratio = max(0.02, mf / total)
        for o in bpy.data.objects:
            if o.type == "MESH":
                o.modifiers.new("dec", "DECIMATE").ratio = ratio
# Downscale textures too: photogrammetry scans bake huge images (the real GLB-weight driver, not
# the face count). Cap the longest side so the embedded GLB textures are gallery-weight.
mt = a.get("max_tex") or 0
if mt:
    for img in list(bpy.data.images):
        w, h = img.size
        if w and h and max(w, h) > mt:
            s = mt / max(w, h)
            try:
                img.scale(max(1, int(w * s)), max(1, int(h * s)))
            except Exception as e:
                print("tex scale skip", img.name, e)
bpy.ops.export_scene.gltf(filepath=a["out"], export_format="GLB", use_selection=False,
                          export_apply=True)
"""


def main() -> int:
    import argparse
    import json
    import os
    import socket
    import subprocess
    import tempfile
    import urllib.request
    import zipfile

    socket.setdefaulttimeout(60)  # urlretrieve has no timeout arg; bound it via the socket default

    from app import recon_service
    from app.database import SessionLocal

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--blender", default=os.environ.get("BLENDER_BIN", str(Path.home() / "blender/blender"))
    )
    ap.add_argument(
        "--crop", default="tomato", choices=sorted(CROPS), help="which crop's curated asset set"
    )
    ap.add_argument(
        "--max-faces",
        type=int,
        default=0,
        help="decimate meshes above this face budget (0 = off; e.g. 150000 for heavy scans)",
    )
    ap.add_argument(
        "--max-tex",
        type=int,
        default=0,
        help="downscale textures to this longest-side px (0 = off; e.g. 2048 for heavy scans)",
    )
    ap.add_argument(
        "--only",
        default="",
        help="comma-separated variant slugs to ingest (default: the crop's whole set)",
    )
    ap.add_argument("--no-score", action="store_true")
    args = ap.parse_args()

    crop = CROPS[args.crop]
    only = {v.strip() for v in args.only.split(",") if v.strip()}
    assets = [a for a in crop["assets"] if not only or a["variant"] in only]

    token = os.environ.get("SKETCHFAB_TOKEN")
    if not token:
        tok_file = Path.home() / ".config/sketchfab/token"
        if tok_file.exists():
            token = tok_file.read_text().strip()
    if not token:
        print("no Sketchfab token (set SKETCHFAB_TOKEN or ~/.config/sketchfab/token)")
        return 1
    if not Path(args.blender).exists():
        print(f"Blender binary not found: {args.blender}")
        return 1

    work = Path(tempfile.mkdtemp(prefix="sketchfab_"))
    script = work / "convert.py"
    script.write_text(_BLENDER_CONVERT)
    items = []
    for asset in assets:
        uid = asset["uid"]
        try:
            req = urllib.request.Request(
                f"https://api.sketchfab.com/v3/models/{uid}/download",
                headers={"Authorization": f"Token {token}"},
            )
            url = (
                json.loads(urllib.request.urlopen(req, timeout=30).read())
                .get("gltf", {})
                .get("url")
            )
            if not url:
                print(f"  {asset['variant']}: no gltf download url")
                continue
            zpath = work / f"{uid}.zip"
            urllib.request.urlretrieve(url, zpath)
            adir = work / uid
            with zipfile.ZipFile(zpath) as z:
                z.extractall(adir)
            glb = work / f"{asset['variant']}.glb"
            gltfs = list(adir.rglob("*.gltf")) or list(adir.rglob("*.glb"))
            if not gltfs:
                print(f"  {asset['variant']}: no glTF in archive")
                continue
            subprocess.run(
                [
                    args.blender,
                    "-b",
                    "-P",
                    str(script),
                    "--",
                    json.dumps(
                        {
                            "src": str(gltfs[0]),
                            "out": str(glb),
                            "keep": asset["keep"],
                            "max_faces": args.max_faces,
                            "max_tex": args.max_tex,
                        }
                    ),
                ],
                check=True,
                timeout=300,
            )
            if glb.exists():
                items.append((str(glb), asset))
        except Exception as e:  # noqa: BLE001 — one asset never aborts the batch
            print(f"  {asset['variant']} fetch/convert failed: {type(e).__name__}: {e}")
    if not items:
        print("no Sketchfab assets fetched")
        return 1

    db = SessionLocal()
    try:
        report = ingest_sketchfab(
            db,
            items,
            to_glb=lambda p: Path(p).read_bytes(),
            score_fn=None if args.no_score else recon_service.score_and_store,
            task_title=crop["task_title"],
        )
        print(report)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
