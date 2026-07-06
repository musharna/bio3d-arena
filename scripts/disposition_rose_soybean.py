# scripts/disposition_rose_soybean.py
"""Disposition after the display-gate loosening: SHOW the good non-CC-input rose/soybean recon
(un-hide) and HIDE the weak CC-swap recon. Rule-based (no hard-coded ids). Study-safe: refuses
to run against the study DB path directly — snapshot + operate on a copy, then promote."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

from sqlalchemy import select

from app.database import SessionLocal
from app.models import ModelOutput, Task

_TAXA = {
    "rose": ("Rosa — single-image → 3D reconstruction", "rose"),
    "soybean": ("Glycine max — single-image → 3D reconstruction", "soybean"),
}


def _input_of(o: ModelOutput) -> str | None:
    try:
        return (json.loads(o.meta_json or "{}") or {}).get("input_image")
    except (ValueError, TypeError):
        return None


def plan_disposition(db) -> dict:
    unhide: list[int] = []
    hide: list[int] = []
    for _key, (title, slug) in _TAXA.items():
        t = db.execute(select(Task).where(Task.title == title)).scalars().first()
        if t is None:
            continue
        for o in db.execute(select(ModelOutput).where(ModelOutput.task_id == t.id)).scalars():
            img = _input_of(o)
            if img is None:
                continue
            if img.endswith(f"{slug}_ref_clean.jpg"):
                hide.append(o.id)  # weak CC-swap recon → hide
            elif img.endswith(f"{slug}_ref.jpg"):
                unhide.append(o.id)  # good original-input recon → show
    return {"unhide": sorted(unhide), "hide": sorted(hide)}


def apply_disposition(db, plan: dict) -> None:
    now = dt.datetime.now(dt.timezone.utc)
    for oid in plan["unhide"]:
        o = db.get(ModelOutput, oid)
        if o is not None:
            o.hidden_at = None
    for oid in plan["hide"]:
        o = db.get(ModelOutput, oid)
        if o is not None and o.hidden_at is None:
            o.hidden_at = now
    db.commit()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="apply (default: dry-run print)")
    ap.parse_args()
    url = os.environ.get("BIO3D_DATABASE_URL", "")
    if "arena-study.db" in url and "PRE-" not in url and "copy" not in url:
        print("refusing to run against the study DB directly — copy it first", file=sys.stderr)
        return 2
    db = SessionLocal()
    plan = plan_disposition(db)
    print(json.dumps(plan, indent=2))
    if "--apply" in sys.argv:
        apply_disposition(db, plan)
        print(f"APPLIED: un-hid {len(plan['unhide'])}, hid {len(plan['hide'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
