"""Ingest a volumetric scan (CT / MRI / X-ray) as a `<modality>:<dataset>` entry — a new
sensor axis for the arena. `ingest_volumetric` is the testable core (volume→GLB + scorer
injected); `main()` (added later) wires app.volume_convert + a local volume dir.

The pilot dataset is barley root MRI (IPK e!DAL, CC-BY-4.0) — a cereal stand-in for the
verified maize-volumetric gap (no open maize anatomy volume exists). See
docs/superpowers/specs/2026-06-23-volumetric-modality-design.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app import ingest  # noqa: E402
from app.models import Task  # noqa: E402
from app.sourcing import VOLUMETRIC_DATASETS  # noqa: E402
from app.volume_convert import VolumeConvertError  # noqa: E402


def ingest_volumetric(
    db,
    volume_paths,
    *,
    dataset,
    to_glb,
    score_fn=None,
    task_title,
    modality="MRI",
    limit=5,
) -> dict:
    """Host each volume as source=`<modality.lower()>:<dataset>` on the subject task."""
    meta_d = VOLUMETRIC_DATASETS[dataset]
    source = f"{modality.lower()}:{dataset}"
    task = db.execute(select(Task).where(Task.title == task_title)).scalars().first()
    if task is None:
        raise RuntimeError(f"subject task not found: {task_title!r}")
    report = {"hosted": 0, "skipped": 0, "errors": 0, "by_depiction": {}}
    for path in list(volume_paths)[:limit]:
        vid = Path(path).stem
        try:
            try:
                glb = to_glb(path)
            except VolumeConvertError as e:
                print(f"  skip {vid}: {e}")
                report["skipped"] += 1
                continue
            depiction = "root_system"
            out, _created = ingest.register_output(
                db,
                task_id=task.id,
                generator_slug=source,
                generator_name=meta_d["name"],
                data=glb,
                ext="glb",
                title=f"{meta_d['name']} — {vid}",
                meta={
                    "depiction": depiction,
                    "dataset": dataset,
                    "modality": modality,
                    "render": "mesh",
                    "caveat": "approximate marching-cubes iso-surface (threshold-dependent); "
                    "cereal stand-in for the maize volumetric gap",
                },
            )
            out.source = source
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
        except Exception as e:  # noqa: BLE001 — one bad volume never aborts the batch
            print(f"  error {vid}: {e}")
            report["errors"] += 1
            db.rollback()
    return report
