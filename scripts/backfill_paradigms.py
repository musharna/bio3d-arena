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


def classify(slug: str, kind: str, sources: set[str]) -> str | None:
    """Return a paradigm for a generator, or None if no rule matches. Order matters:
    most-specific families first."""
    s = slug.lower()
    src = {x.lower() for x in sources}

    def any_in(needles, hay):
        return any(n in h for n in needles for h in hay)

    if s.startswith("openrouter-"):
        return "procedural_llm"
    if any(k in s for k in ("lpy", "l-py", "lsystem", "infinigen", "procedural")):
        return "procedural_expert"
    if "sketchfab" in s or "objaverse" in s or any_in(("sketchfab", "objaverse"), src):
        return "retrieval"
    if any(k in s for k in ("icrisat", "romi", "scan")) or any_in(
        ("icrisat", "romi", "scan", "reference"), src
    ):
        return "capture_scan"
    if any(k in s for k in ("hunyuan", "tripo", "partcrafter", "meshy", "trellis", "recon")) or any(
        h.startswith("api:") for h in src
    ):
        return "image_recon"
    return None


def assign_paradigms(db, *, commit: bool) -> dict:
    gens = db.execute(select(Generator)).scalars().all()
    assigned: dict[str, str] = {}
    unmapped: list[str] = []
    for g in gens:
        sources = {
            o.source
            for o in db.execute(
                select(ModelOutput).where(ModelOutput.generator_id == g.id)
            ).scalars()
        }
        p = classify(g.slug, g.kind, sources)
        if p is None:
            unmapped.append(g.slug)
        else:
            assigned[g.slug] = p
    if commit:
        if unmapped:
            raise ValueError(f"unmapped generators (refusing to write): {sorted(unmapped)}")
        for g in gens:
            g.paradigm = assigned[g.slug]
        db.commit()
    return {"assigned": assigned, "unmapped": unmapped}


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
