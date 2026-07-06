"""Component D backfill: scan every visible recon output's INPUT photo and flag species mismatches
(multi-class BioCLIP; 2026-07-06 probe: 13/13). Advisory-only — never auto-hides. Read-only unless
--apply. GPU inference; run via jobd on a GPU host for the full sweep.

Usage:
  python scripts/verify_input_subjects.py            # dry-run: print the triage list
  python scripts/verify_input_subjects.py --apply    # record non-hiding advisory flags
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402
from app import input_verify, species_id  # noqa: E402
from app.storage import get_storage  # noqa: E402


def _taxon_of(output) -> str | None:
    """Claimed binomial from the output's task title prefix (before the em-dash)."""
    task = getattr(output, "task", None)
    if task is None or not task.title:
        return None
    return task.title.split("—")[0].strip() or None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="record advisory flags (else dry-run)")
    ap.add_argument("--margin", type=float, default=0.0, help="min top-1 margin to accept a claim")
    args = ap.parse_args()

    if args.apply and not config.is_safe_test_db_target(config.DATABASE_URL):
        # --apply writes flags; refuse to touch a real study DB unless it is an explicit op target.
        # (Flags are advisory/non-hiding, but writes still gate on the canonical guard.)
        print(f"REFUSING --apply on non-test DB: {config.DATABASE_URL}", file=sys.stderr)
        # Allow an explicit override for the intended production backfill.
        if "--i-mean-it" not in sys.argv:
            print("re-run with --i-mean-it to backfill the real study DB", file=sys.stderr)
            return 2

    if not species_id.available():
        print("ERROR: open_clip not installed — cannot classify.", file=sys.stderr)
        return 1

    from app.database import SessionLocal

    bundle = species_id.load_model("bioclip")
    store = get_storage()

    def resolve_png(rel):
        try:
            return store.read(rel)
        except Exception:
            return None

    with SessionLocal() as db:
        triage = input_verify.scan_and_flag(
            db,
            bundle=bundle,
            resolve_png=resolve_png,
            taxon_of=_taxon_of,
            apply=args.apply,
            min_margin=args.margin,
        )

    print(
        f"{len(triage)} input-subject mismatch(es){' (flagged)' if args.apply else ' (dry-run)'}:"
    )
    for t in triage:
        print(
            f"  output {t['output_id']}: claims {t['claimed']!r}, reads as {t['reads_as']!r} "
            f"(p={t['prob']}) — {t['input_image']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
