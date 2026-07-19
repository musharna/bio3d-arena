"""Classify every existing Generator into a paradigm and set Generator.paradigm.

Dry-run by default (prints the generator->paradigm table + any unmapped). `--commit` writes,
and REFUSES to write if any generator is unmapped (fail loud — never default-assign). Run
against the target DB via BIO3D_DATABASE_URL; classification uses the generator slug + the
set of `source` strings across its outputs."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import Generator, ModelOutput  # noqa: E402
from app.paradigms import classify_paradigm as classify  # noqa: E402  (shared with promote/ingest)


def assign_paradigms(db, *, commit: bool) -> dict:
    gens = db.execute(select(Generator)).scalars().all()
    assigned: dict[str, str] = {}
    unmapped: list[str] = []
    skipped: list[str] = []
    for g in gens:
        if g.kind in ("decoy", "gold"):
            skipped.append(g.slug)
            continue
        sources = {
            o.source
            for o in db.execute(
                select(ModelOutput).where(ModelOutput.generator_id == g.id)
            ).scalars()
        }
        p = classify(g.slug, g.kind, sources)
        if p is None:
            if not sources:  # 0-output, unclassifiable — not a ranking competitor
                skipped.append(g.slug)
                continue
            unmapped.append(g.slug)
        else:
            assigned[g.slug] = p
    if commit:
        if unmapped:
            raise ValueError(f"unmapped generators (refusing to write): {sorted(unmapped)}")
        for g in gens:
            if g.slug in assigned:
                g.paradigm = assigned[g.slug]
        db.commit()
    return {"assigned": assigned, "unmapped": unmapped, "skipped": skipped}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--commit", action="store_true", help="write paradigms (else dry-run)")
    args = ap.parse_args(argv)
    with SessionLocal() as db:
        res = assign_paradigms(db, commit=args.commit)
    for slug, p in sorted(res["assigned"].items()):
        print(f"  {slug:40s} -> {p}")
    if res["unmapped"]:
        print(f"\nUNMAPPED ({len(res['unmapped'])}): {sorted(res['unmapped'])}")
        print("Add rules for these before --commit.")
        return 0 if not args.commit else 1
    print(
        f"\n{len(res['assigned'])} generators classified; unmapped: 0"
        + ("" if args.commit else "  (dry run — re-run with --commit)")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
