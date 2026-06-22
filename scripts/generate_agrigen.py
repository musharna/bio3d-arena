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
AGRIGEN_LICENSE = "AgriGen (internal research generator — not for redistribution)"
AGRIGEN_ATTRIBUTION = "AgriGen UnifiedGenerator (Solanum lycopersicum plant descriptor)"
# Honest caveat: AgriGen is a coherent L-system + neural-leaf plant, but its neural leaf blades are
# rounded lobes (not serrated tomato leaflets), it has no fruit in this config, and low fidelity.
AGRIGEN_CAVEAT = "L-system + neural-leaf — rounded leaflets, no fruit, low fidelity"


def ingest_agrigen(
    db,
    glb_paths,
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
            out.attribution = f"{AGRIGEN_ATTRIBUTION} [{variant}]"
            out.external_url = ""
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
    from scripts.agrigen_glb import recolor_leaves

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--agrigen-dir",
        default=os.environ.get("AGRIGEN_DIR", str(Path.home() / "agrigen")),
        help="path to the AgriGen checkout (or set AGRIGEN_DIR)",
    )
    ap.add_argument("-n", type=int, default=1, help="number of seeds/plants to generate")
    ap.add_argument("--no-score", action="store_true")
    ap.add_argument("--no-recolor", action="store_true", help="keep AgriGen's authored leaf color")
    ap.add_argument("--caveat", default=AGRIGEN_CAVEAT, help="caveat badge text (empty to omit)")
    args = ap.parse_args()

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
        glb = str(Path(out_dir) / f"tomato_{seed}.glb")
        try:
            subprocess.run(
                [str(venv_py), str(runner), glb, str(seed)],
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
        return data if args.no_recolor else recolor_leaves(data)

    db = SessionLocal()
    try:
        report = ingest_agrigen(
            db,
            glbs,
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
