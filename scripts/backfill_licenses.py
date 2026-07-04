"""Correct/assign model_output.license on a DB COPY before public export (idempotent, fail-loud).
NEVER run against the real study DB. Own/LLM/procedural -> CC0; crops3d -> CC0 (verified Figshare
data-record); Objaverse -> resolved per-uid license; space-form CC -> normalized SPDX. The three
hard-excludes (xfrog/demeter/agrigen) are left untouched (stay non-redistributable)."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.licensing import normalize_license  # noqa: E402
from app.models import ModelOutput  # noqa: E402
from sqlalchemy import select  # noqa: E402

CC0 = "CC0-1.0"
HARD_EXCLUDE_SOURCES = {"found:xfrog", "procedural:demeter", "procedural:agrigen"}
_OWN_CC0_PREFIXES = ("bio3d-arena", "commissioned", "agentic:", "procedural:", "infinigen")


def _is_own_cc0(source: str | None) -> bool:
    s = source or ""
    if s in HARD_EXCLUDE_SOURCES:
        return False
    return (
        s == "bio3d-arena"
        or s == "commissioned"
        or s == "infinigen"
        or s.startswith(("agentic:", "procedural:"))
    )


def backfill_licenses(db, *, objaverse_license_for: Callable[[str], str | None]) -> dict:
    disp: Counter = Counter()
    outs = db.execute(select(ModelOutput).where(ModelOutput.is_gold.is_(False))).scalars().all()
    for o in outs:
        src = o.source or ""
        if src in HARD_EXCLUDE_SOURCES:
            disp["hard_exclude_untouched"] += 1
            continue
        if src == "objaverse":
            uid = (json.loads(o.meta_json or "{}")).get("objaverse_uid")
            code = objaverse_license_for(uid) if uid else None
            norm = normalize_license(code) if code else None
            if norm:
                o.license = norm
                disp[f"objaverse:{norm}"] += 1
            else:
                disp["objaverse_unresolved"] += 1  # stays as-is -> non-allowlisted -> gate excludes
            continue
        if src == "crops3d":
            o.license = CC0
            o.attribution = (
                o.attribution or ""
            ) or "Crops3D — Figshare data-record CC0 (art. 27313272)"
            disp["crops3d->CC0"] += 1
            continue
        if _is_own_cc0(src):
            o.license = CC0
            disp["own_or_llm_or_procedural->CC0"] += 1
            continue
        # everything else (external CC datasets, sketchfab, api:* commercial-model): normalize only
        norm = normalize_license(o.license)
        if norm and norm != o.license:
            o.license = norm
            disp[f"normalized:{norm}"] += 1
    return dict(disp)


def _objaverse_lookup(uids: set[str]) -> Callable[[str], str | None]:
    try:
        import objaverse  # type: ignore
    except Exception:
        raise SystemExit(
            "objaverse package not installed; `pip install objaverse` to resolve per-uid licenses"
        )
    anns = objaverse.load_annotations(list(uids))
    return lambda uid: (anns.get(uid) or {}).get("license")


def main() -> int:
    ap = argparse.ArgumentParser(description="Backfill model_output.license on a DB COPY.")
    ap.add_argument("--commit", action="store_true", help="persist changes (default: dry-run)")
    args = ap.parse_args()
    if "study" in (config.DATABASE_URL or "").lower():
        raise SystemExit("refusing to run against a 'study' DB — use a copy")
    with SessionLocal() as db:
        uids = {
            json.loads(o.meta_json or "{}").get("objaverse_uid")
            for o in db.execute(select(ModelOutput).where(ModelOutput.source == "objaverse"))
            .scalars()
            .all()
        }
        uids = {u for u in uids if u}
        lookup = _objaverse_lookup(uids) if uids else (lambda uid: None)
        disp = backfill_licenses(db, objaverse_license_for=lookup)
        if args.commit:
            db.commit()
        print(json.dumps({"committed": args.commit, "disposition": disp}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
