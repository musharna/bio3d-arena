"""Ingest CC-licensed game-ready tomato-PLANT assets from Sketchfab as `found:sketchfab` entries.

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

# Verified on the Sketchfab API (license slug + isDownloadable) 2026-06-22. `keep` isolates the
# fruiting-stage mesh for the multi-stage PolyOne pack (drops the title text + earlier stages).
ASSETS = [
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
bpy.ops.export_scene.gltf(filepath=a["out"], export_format="GLB", use_selection=False)
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
    ap.add_argument("--no-score", action="store_true")
    args = ap.parse_args()

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
    for asset in ASSETS:
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
                        {"src": str(gltfs[0]), "out": str(glb), "keep": asset["keep"]}
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
        )
        print(report)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
