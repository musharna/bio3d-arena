"""Reproducible SP4 results analysis: from the human trait-calibration labels + Chamfer metrics,
quantify (a) trait-judgment failure rate, (b) morphological-incompleteness of generated outputs vs
trait-unjudgeability, (c) the geometry-GT blind spot, (d) taxon-dependence.

Run from the repo root against a data dir holding data/study/calibration_labels_*_filled.csv and
a study DB:  BIO3D_DATABASE_URL=sqlite:///data/study/arena-study.db python -m scripts.analyze_trait_calibration
Read-only. Categorization rules are explicit + raw counts are printed so every number is auditable.
"""

from __future__ import annotations

import collections
import csv
import os
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DATA = Path(os.environ.get("BIO3D_DATA_DIR", "data"))
FILES = [
    DATA / "study/calibration_labels_filled.csv",
    DATA / "study/calibration_labels_morph_filled.csv",
    DATA / "study/calibration_labels_morph_r2_filled.csv",
]

INCOMPLETE = (
    "not a plant",
    "junk",
    "just sticks",
    "only fruit",
    "not a full plant",
    "stub",
    "seedling",
    "leafless",
    "fruitless",
    "partial",
    "just a fruit",
    "missing rest",
)
TRAITBAD = (
    "weird trait",
    "bad trait",
    "n/a trait",
    "debatable",
    "what the hell",
    "wtf",
    "trait imo",
    "bad teait",
    "simple meaning",
    "this trait",
    "weird traut",
)
LOWRES = ("low-res", "too low", "no color", "low res", "not assessable via image", "resolution")


def reason(note: str) -> str:
    n = (note or "").strip().lower()
    if not n:
        return "unspecified"
    if any(k in n for k in INCOMPLETE):
        return "output_incomplete"
    if any(k in n for k in TRAITBAD):
        return "trait_unjudgeable"
    if any(k in n for k in LOWRES):
        return "low_res"
    return "other/uncertain"


def main() -> int:
    rows = []
    for f in FILES:
        if f.exists():
            rows += list(csv.DictReader(open(f)))
    if not rows:
        print("no calibration CSVs found under", DATA / "study")
        return 1
    n = len(rows)
    verdicts = collections.Counter((r["human_verdict"] or "").strip().lower() for r in rows)
    na = [r for r in rows if (r["human_verdict"] or "").strip().lower() == "not_assessable"]

    print(f"=== n={n} literature-trait x generated-output labelings ===")
    for v, c in verdicts.most_common():
        print(f"  {v:18s} {c:4d}  ({100 * c / n:4.1f}%)")
    print(f"\nNOT_ASSESSABLE = {len(na)}/{n} = {100 * len(na) / n:.1f}%")
    print("\nwhy not_assessable (categorized from the note):")
    for v, c in collections.Counter(reason(r["note"]) for r in na).most_common():
        print(f"  {v:18s} {c:4d}  ({100 * c / len(na):4.1f}% of NA, {100 * c / n:4.1f}% of all)")

    inc_out = {r["output_id"] for r in na if reason(r["note"]) == "output_incomplete"}
    allo = {r["output_id"] for r in rows}
    print(
        f"\noutputs flagged morphologically incomplete: {len(inc_out)}/{len(allo)} "
        f"({100 * len(inc_out) / len(allo):.1f}%)"
    )

    print("\nnot_assessable by taxon:")
    by = collections.defaultdict(lambda: [0, 0])
    for r in rows:
        t = r["taxon"] or "?"
        by[t][1] += 1
        by[t][0] += (r["human_verdict"] or "").strip().lower() == "not_assessable"
    for t, (c, tot) in sorted(by.items(), key=lambda x: -x[1][1]):
        print(f"  {t:24s} {c:3d}/{tot:3d}  ({100 * c / tot:4.1f}%)")

    from app.database import SessionLocal  # noqa: E402
    from app.models import Metric  # noqa: E402
    from sqlalchemy import select  # noqa: E402

    db = SessionLocal()
    chamfer = {
        m.output_id: m.chamfer
        for m in db.execute(select(Metric)).scalars()
        if m.chamfer is not None
    }
    db.close()

    def _int(x):
        try:
            return int(x)
        except Exception:
            return None

    inc_ids = {_int(o) for o in inc_out} & set(chamfer)
    other = set(chamfer) - {_int(o) for o in inc_out}
    print(f"\ngeometry-GT blind spot (Chamfer, lower=better; {len(chamfer)} scored):")
    if inc_ids and other:
        inc = [chamfer[i] for i in inc_ids]
        oth = [chamfer[i] for i in other]
        print(f"  incomplete-flagged (n={len(inc)}): median Chamfer {st.median(inc):.4f}")
        print(f"  others (n={len(oth)}):            median Chamfer {st.median(oth):.4f}")
        below = sum(1 for c in inc if c <= st.median(oth))
        print(
            f"  -> {below}/{len(inc)} incomplete outputs at/below the others' median Chamfer "
            f"(geometry does not flag them)."
        )
    else:
        print("  (no overlap between incomplete-flagged and Chamfer-scored outputs.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
