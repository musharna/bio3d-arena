"""Generate a procedural tomato with AgriGen's UnifiedGenerator and ingest it as a
'procedural:agrigen' entry. `ingest_agrigen` is the testable core (GLB bytes + scorer injected);
`main()` subprocesses AgriGen's own venv to run `agrigen_runner.py` (AgriGen is consumed READ-ONLY,
never edited or imported into this venv), recolors the leaf organs, then ingests. Commits per object.
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
AGRIGEN_LICENSE = "AgriGen (internal research generator — not for redistribution)"
AGRIGEN_ATTRIBUTION = "AgriGen UnifiedGenerator (Solanum lycopersicum plant descriptor)"
# Honest caveat: AgriGen is a coherent L-system + neural-leaf plant, but its neural leaf blades are
# rounded lobes (not serrated tomato leaflets), it has no fruit in this config, and low fidelity.
AGRIGEN_CAVEAT = "L-system + neural-leaf — rounded leaflets, no fruit, low fidelity"

# Per-crop config. Each runs AgriGen's UnifiedGenerator against that species' plant descriptor.
# Maize verdict (gated 2026-06-22): the zea_mays PD yields a tall culm of SHORT spiky leaves (not
# the long broad arching straps of real maize) with no tassel/ear — caveat-class, like the tomato.
AGRIGEN_CROPS = {
    "tomato": {
        "variant": "tomato",
        "task_title": TOMATO_TITLE,
        "attribution": AGRIGEN_ATTRIBUTION,
        "caveat": AGRIGEN_CAVEAT,
    },
    "maize": {
        "variant": "maize",
        "task_title": MAIZE_TITLE,
        "attribution": "AgriGen UnifiedGenerator (Zea mays plant descriptor)",
        "caveat": "L-system + neural-leaf — short spiky leaves (not long maize straps), "
        "no tassel/ear, low fidelity",
    },
}


def ingest_agrigen(
    db,
    glb_paths,
    *,
    to_glb,
    score_fn=None,
    variant="tomato",
    task_title=TOMATO_TITLE,
    attribution=AGRIGEN_ATTRIBUTION,
    limit=10,
    caveat=None,
) -> dict:
    task = db.execute(select(Task).where(Task.title == task_title)).scalars().first()
    if task is None:
        raise RuntimeError(f"subject task not found: {task_title!r}")
    report = {"hosted": 0, "skipped": 0, "errors": 0, "by_variant": {}}
    for path in list(glb_paths)[:limit]:
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
                generator_slug="agrigen",
                generator_name="AgriGen",
                data=glb,
                ext="glb",
                title=asset_id,
                meta=meta,
            )
            out.source = "procedural:agrigen"
            out.license = AGRIGEN_LICENSE
            out.attribution = f"{attribution} [{variant}]"
            out.external_url = None  # internal generator: hosted locally, no external link
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
    from scripts.agrigen_glb import recolor_leaves, reorient_upright

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--agrigen-dir",
        default=os.environ.get("AGRIGEN_DIR", str(Path.home() / "agrigen")),
        help="path to the AgriGen checkout (or set AGRIGEN_DIR)",
    )
    ap.add_argument("-n", type=int, default=1, help="number of seeds/plants to generate")
    ap.add_argument("--crop", default="tomato", choices=sorted(AGRIGEN_CROPS))
    ap.add_argument("--no-score", action="store_true")
    ap.add_argument("--no-recolor", action="store_true", help="keep AgriGen's authored leaf color")
    ap.add_argument(
        "--caveat",
        default=None,
        help="caveat badge text (default: the crop's caveat; empty string to omit)",
    )
    args = ap.parse_args()

    crop = AGRIGEN_CROPS[args.crop]
    caveat = crop["caveat"] if args.caveat is None else args.caveat

    backend = Path(args.agrigen_dir) / "backend"
    venv_py = backend / ".venv/bin/python"
    runner = Path(__file__).resolve().parent / "agrigen_runner.py"
    if not venv_py.exists():
        print(f"AgriGen venv python not found: {venv_py} — install AgriGen's backend venv first")
        return 1

    out_dir = tempfile.mkdtemp(prefix="agrigen_")
    glbs = []
    env = {**os.environ, "PYTHONPATH": str(backend)}
    for seed in range(args.n):
        glb = str(Path(out_dir) / f"{args.crop}_{seed}.glb")
        try:
            subprocess.run(
                [str(venv_py), str(runner), glb, str(seed), "20000", "--crop", args.crop],
                check=True,
                timeout=600,
                env=env,
            )
            if Path(glb).exists():
                glbs.append(glb)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
            print(f"  agrigen seed {seed} failed: {e}")
    if not glbs:
        print(f"no .glb produced under {out_dir}")
        return 1

    def to_glb(path: str) -> bytes:
        data = Path(path).read_bytes()
        if not args.no_recolor:
            data = recolor_leaves(data)
        # AgriGen writes plants growing along glTF +Z; stand them up for +Y-up <model-viewer>.
        return reorient_upright(data)

    db = SessionLocal()
    try:
        report = ingest_agrigen(
            db,
            glbs,
            to_glb=to_glb,
            score_fn=None if args.no_score else recon_service.score_and_store,
            variant=crop["variant"],
            task_title=crop["task_title"],
            attribution=crop["attribution"],
            caveat=caveat or None,
        )
        print(report)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
