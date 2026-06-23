"""Source license-vetted crop 3D models from Objaverse onto a crop's spotlight Task.

`ingest_found` is the testable core (Objaverse access + scorer injected). `main()` wires
the real `objaverse` package + the recon scorer. Hosts any CC/public-domain license,
excludes all-rights-reserved/unmarked (see app.sourcing). Commits per object.

Per-crop note: tomato's LVIS "tomato" category carries whole plants AND fruits; maize's
only category is `edible_corn` — corn EARS/cobs (the harvested fruit), no whole plants. So
the maize pull is restricted to public-safe ears and labelled depiction='fruit' (the ear,
not the plant); the whole-maize-plant `found` coverage comes from Sketchfab + Xfrog instead.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app import ingest  # noqa: E402
from app.models import Task  # noqa: E402
from app.sourcing import classify_license, label_depiction, public_safe  # noqa: E402

TOMATO_TITLE = "Solanum lycopersicum — single-image → 3D reconstruction"
MAIZE_TITLE = "Zea mays — single-image → 3D reconstruction"

# Per-crop: subject task, the LVIS category keyword, name include/exclude filters (LVIS is noisy —
# tomato pulls apples/persimmons, edible_corn pulls corn dogs/cornbread/canned corn), whether to
# restrict to public-safe CC (no NC/ND), and an optional depiction override. Defaults below
# preserve the original tomato behaviour (host any CC, depiction from the name).
CROPS = {
    "tomato": {
        "task_title": TOMATO_TITLE,
        "lvis_keyword": "tomato",
        "name_includes": ("tomato",),
        "name_excludes": (),
        "require_public_safe": False,
        "depiction_override": None,
    },
    "maize": {
        "task_title": MAIZE_TITLE,
        "lvis_keyword": "edible_corn",
        "name_includes": ("corn", "maize"),
        "name_excludes": (
            "corn dog",
            "corndog",
            "cornbread",
            "corn bread",
            "cornmeal",
            "canned",
            "can of",
            "soup",
            "popcorn",
            "dough",
            "scrap mechanic",
        ),
        "require_public_safe": True,  # ears are decorative game assets; keep the maize set clean CC
        "depiction_override": "fruit",  # a corn ear is the fruit/infructescence, not a whole plant
    },
}


def ingest_found(
    db,
    uids,
    *,
    fetch_annotations,
    fetch_objects,
    score_fn=None,
    task_title=TOMATO_TITLE,
    name_includes=("tomato",),
    name_excludes=(),
    require_public_safe=False,
    depiction_override=None,
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
        # Relevance: the LVIS category is noisy (tomato pulls apples/persimmons; edible_corn pulls
        # corn dogs/cornbread/canned corn), so require an on-subject word and drop the known noise.
        # A precision filter for a one-shot, human-reviewed pull — it may drop a legit object whose
        # name lacks the word; the /spotlight grid is hand-inspected, so precision > recall here.
        lname = name.lower()
        if not any(k in lname for k in name_includes) or any(x in lname for x in name_excludes):
            report["off_subject"] += 1
            continue
        lic = ann.get("license")
        ok = public_safe(lic) if require_public_safe else classify_license(lic) == "host"
        if not ok:
            report["excluded"] += 1
            key = lic or "unmarked"
            report["excluded_licenses"][key] = report["excluded_licenses"].get(key, 0) + 1
            continue
        depiction = depiction_override or label_depiction(name)
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
    ap.add_argument(
        "--crop", default="tomato", choices=sorted(CROPS), help="which crop's LVIS category to pull"
    )
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--no-score", action="store_true", help="skip GT scoring of whole-plant")
    args = ap.parse_args()

    crop = CROPS[args.crop]
    kw = crop["lvis_keyword"]
    lvis = objaverse.load_lvis_annotations()
    uids = []
    for cat, cat_uids in lvis.items():
        if kw in cat.lower():
            uids.extend(cat_uids)
    uids = uids[: args.limit]
    if not uids:
        print(f"no {kw!r} LVIS category uids found")
        return 0

    db = SessionLocal()
    try:
        report = ingest_found(
            db,
            uids,
            fetch_annotations=objaverse.load_annotations,
            fetch_objects=lambda u: objaverse.load_objects(u),
            score_fn=None if args.no_score else recon_service.score_and_store,
            task_title=crop["task_title"],
            name_includes=crop["name_includes"],
            name_excludes=crop["name_excludes"],
            require_public_safe=crop["require_public_safe"],
            depiction_override=crop["depiction_override"],
        )
        print(report)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
