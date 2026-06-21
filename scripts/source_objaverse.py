"""Source license-vetted tomato 3D models from Objaverse onto the tomato spotlight Task.

`ingest_found` is the testable core (Objaverse access + scorer injected). `main()` wires
the real `objaverse` package + the recon scorer. Hosts any CC/public-domain license,
excludes all-rights-reserved/unmarked (see app.sourcing). Commits per object.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app import ingest  # noqa: E402
from app.models import Task  # noqa: E402
from app.sourcing import classify_license, label_depiction  # noqa: E402

TOMATO_TITLE = "Solanum lycopersicum — single-image → 3D reconstruction"


def ingest_found(
    db,
    uids,
    *,
    fetch_annotations,
    fetch_objects,
    score_fn=None,
    task_title=TOMATO_TITLE,
) -> dict:
    task = db.execute(select(Task).where(Task.title == task_title)).scalars().first()
    if task is None:
        raise RuntimeError(f"subject task not found: {task_title!r}")
    report = {
        "hosted": 0,
        "excluded": 0,
        "off_subject": 0,
        "by_depiction": {},
        "excluded_licenses": {},
    }
    anns = fetch_annotations(list(uids))
    for uid in uids:
        ann = anns.get(uid) or {}
        name = ann.get("name") or uid
        # Relevance: the LVIS "tomato" category is noisy (apples, persimmons), so require
        # the word in the name. This is a precision filter for a one-shot, human-reviewed
        # pull — it may drop a legit tomato whose name lacks the word; the /spotlight grid
        # is hand-inspected, so favoring precision over recall is the right tradeoff here.
        if "tomato" not in name.lower():
            report["off_subject"] += 1
            continue
        lic = ann.get("license")
        if classify_license(lic) != "host":
            report["excluded"] += 1
            key = lic or "unmarked"
            report["excluded_licenses"][key] = report["excluded_licenses"].get(key, 0) + 1
            continue
        depiction = label_depiction(name)
        try:
            glb_path = fetch_objects([uid]).get(uid)
            data = Path(glb_path).read_bytes()
            out, _created = ingest.register_output(
                db,
                task_id=task.id,
                generator_slug="objaverse",
                generator_name="Objaverse",
                data=data,
                ext="glb",
                title=name,
                meta={"depiction": depiction, "objaverse_uid": uid, "found": True},
            )
            out.source = "objaverse"
            out.license = lic
            out.attribution = ann.get("author") or ann.get("user", {}).get("displayName")
            out.external_url = ann.get("viewerUrl") or ann.get("uri")
            db.commit()  # provenance committed → object is hosted
            report["hosted"] += 1
            report["by_depiction"][depiction] = report["by_depiction"].get(depiction, 0) + 1
            if score_fn is not None and depiction == "whole_plant":
                try:
                    score_fn(db, out)
                    db.commit()
                except Exception as e:  # noqa: BLE001 — scoring is best-effort; object stays hosted
                    print(f"  score failed for {out.id}: {e}")
                    db.rollback()
        except Exception as e:  # noqa: BLE001 — best-effort; one bad object never aborts
            print(f"  skip {uid}: {e}")
            db.rollback()
    return report


def main() -> int:
    import argparse

    import objaverse

    from app import recon_service
    from app.database import SessionLocal

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--no-score", action="store_true", help="skip GT scoring of whole-plant")
    args = ap.parse_args()

    lvis = objaverse.load_lvis_annotations()
    uids = []
    for cat, cat_uids in lvis.items():
        if "tomato" in cat.lower():
            uids.extend(cat_uids)
    uids = uids[: args.limit]
    if not uids:
        print("no 'tomato' LVIS category uids found")
        return 0

    db = SessionLocal()
    try:
        report = ingest_found(
            db,
            uids,
            fetch_annotations=objaverse.load_annotations,
            fetch_objects=lambda u: objaverse.load_objects(u),
            score_fn=None if args.no_score else recon_service.score_and_store,
        )
        print(report)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
