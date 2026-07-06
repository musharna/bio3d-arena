# scripts/flag_recon_reliability.py
"""Recon-reliability triage: per taxon, compare image_recon vs text_native mean organism-
completeness and flag taxa where recon is far below text — the reference/capture-quality
signal (text→3D shares the subject but not the reference photo). Would have auto-caught the
Cucurbita reference-photo bug (recon 0.13 vs text 1.00). Read-only; if a generation wave is
writing the study DB, point BIO3D_DATABASE_URL at a copy."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.completeness import recon_reliability_flags  # noqa: E402
from app.database import SessionLocal  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--gap",
        type=float,
        default=0.4,
        help="flag threshold on the (text - recon) mean-completeness gap (default 0.4)",
    )
    args = ap.parse_args()
    with SessionLocal() as db:
        rows = recon_reliability_flags(db, gap_threshold=args.gap)
    if not rows:
        print(
            "no taxa have completeness in BOTH image_recon and text_native yet — nothing to compare"
        )
        return 0
    print(f"{'taxon':30} {'recon':>7} {'text':>7} {'gap':>7}  flag")
    for r in rows:
        mark = "  <= INSPECT reference/capture" if r["flag"] else ""
        print(
            f"{r['taxon'][:30]:30} {r['recon_mean']:7.2f} {r['text_mean']:7.2f} "
            f"{r['gap']:7.2f}   (n_recon={r['n_recon']}, n_text={r['n_text']}){mark}"
        )
    flagged = [r["taxon"] for r in rows if r["flag"]]
    print(f"\n{len(flagged)} flagged for inspection: {flagged}" if flagged else "\nno taxa flagged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
