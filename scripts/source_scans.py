"""Ingest real scanned whole-plant meshes from an academic dataset onto the tomato
spotlight Task. `ingest_scans` is the testable core (mesh→GLB + scorer injected);
`main()` wires a dataset adapter (local mesh glob) + the recon scorer. Commits per object.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app import ingest  # noqa: E402
from app.mesh_convert import MeshConvertError  # noqa: E402
from app.models import Task  # noqa: E402
from app.sourcing import SCAN_DATASETS  # noqa: E402

TOMATO_TITLE = "Solanum lycopersicum — single-image → 3D reconstruction"
MAIZE_TITLE = "Zea mays — single-image → 3D reconstruction"
# Map a CLI crop key → the subject task that real scans of that crop attach to.
ROSE_TITLE = "Rosa — single-image → 3D reconstruction"
SOYBEAN_TITLE = "Glycine max — single-image → 3D reconstruction"
ARABIDOPSIS_TITLE = "Arabidopsis thaliana — single-image → 3D reconstruction"
SCAN_TASKS = {
    "tomato": TOMATO_TITLE,
    "maize": MAIZE_TITLE,
    "rose": ROSE_TITLE,
    "soybean": SOYBEAN_TITLE,
    "arabidopsis": ARABIDOPSIS_TITLE,
}


def ingest_scans(
    db,
    mesh_paths,
    *,
    dataset,
    to_glb,
    score_fn=None,
    task_title=TOMATO_TITLE,
    limit=15,
    render_kind="mesh",
) -> dict:
    meta_d = SCAN_DATASETS[dataset]
    task = db.execute(select(Task).where(Task.title == task_title)).scalars().first()
    if task is None:
        raise RuntimeError(f"subject task not found: {task_title!r}")
    report = {"hosted": 0, "skipped_pointcloud": 0, "errors": 0, "by_depiction": {}}
    for path in list(mesh_paths)[:limit]:
        scan_id = Path(path).stem
        try:
            try:
                glb = to_glb(path)
            except MeshConvertError as e:
                print(f"  skip (point-cloud) {scan_id}: {e}")
                report["skipped_pointcloud"] += 1
                continue
            depiction = "whole_plant"
            out, _created = ingest.register_output(
                db,
                task_id=task.id,
                generator_slug=f"scan:{dataset}",
                generator_name=meta_d["name"],
                data=glb,
                ext="glb",
                title=scan_id,
                meta={
                    "depiction": depiction,
                    "dataset": dataset,
                    "scan_id": scan_id,
                    "render": render_kind,
                },
            )
            out.source = dataset
            out.license = meta_d["license"]
            out.attribution = meta_d["attribution"]
            out.external_url = meta_d["url"]
            db.commit()  # provenance committed → hosted
            report["hosted"] += 1
            report["by_depiction"][depiction] = report["by_depiction"].get(depiction, 0) + 1
            if score_fn is not None and depiction == "whole_plant":
                try:
                    score_fn(db, out)
                    db.commit()
                except Exception as e:  # noqa: BLE001 — scoring best-effort; object stays hosted
                    print(f"  score failed for {out.id}: {e}")
                    db.rollback()
        except Exception as e:  # noqa: BLE001 — one bad mesh never aborts the batch
            print(f"  error {scan_id}: {e}")
            report["errors"] += 1
            db.rollback()
    return report


def main() -> int:
    import argparse

    from app import recon_service
    from app.database import SessionLocal
    from app.mesh_convert import to_glb as _to_glb

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", required=True, choices=sorted(SCAN_DATASETS))
    ap.add_argument("--task", default="tomato", choices=sorted(SCAN_TASKS))
    ap.add_argument("--dir", required=True, help="local dir containing the scan mesh/cloud files")
    ap.add_argument("--limit", type=int, default=15)
    ap.add_argument(
        "--max-faces",
        type=int,
        default=150_000,
        help="decimate meshes above this face budget (0 = full resolution). Laser scans run "
        "to millions of triangles / tens of MB GLB; the default keeps the grid web-viable.",
    )
    ap.add_argument("--no-score", action="store_true")
    ap.add_argument("--render", choices=("mesh", "points"), default="mesh")
    ap.add_argument(
        "--up-axis",
        choices=("none", "z"),
        default="none",
        help="point-cloud source up-axis; 'z' stands +Z-up scans (e.g. Crops3D) upright for +Y viewers",
    )
    args = ap.parse_args()

    root = Path(args.dir)
    if not root.exists():
        print(f"dataset dir not found: {root} — download the dataset first")
        return 1
    # Points path: only formats the installed trimesh can actually load. (.pcd needs an
    # extra loader we deliberately don't add — "no new heavy dependency" — so we don't
    # advertise it; a .pcd dataset would convert via a one-off cloud→.ply step first.)
    exts = ("*.obj", "*.ply", "*.glb") if args.render == "mesh" else ("*.ply", "*.xyz")
    meshes = sorted(str(p) for ext in exts for p in root.rglob(ext))
    if not meshes:
        print(f"no {'/'.join(e.lstrip('*') for e in exts)} files under {root}")
        return 1

    if args.render == "points":
        from app.points_convert import points_to_glb

        up_axis = None if args.up_axis == "none" else args.up_axis

        def to_glb(path: str) -> bytes:
            return points_to_glb(path, up_axis=up_axis)
    else:
        max_faces = args.max_faces or None

        def to_glb(path: str) -> bytes:
            return _to_glb(path, max_faces=max_faces)

    db = SessionLocal()
    try:
        report = ingest_scans(
            db,
            meshes,
            dataset=args.dataset,
            to_glb=to_glb,
            score_fn=None if args.no_score else recon_service.score_and_store,
            task_title=SCAN_TASKS[args.task],
            limit=args.limit,
            render_kind=args.render,
        )
        print(report)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
