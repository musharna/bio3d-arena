"""Generate the VLM↔human calibration report → docs/results/<date>-vlm-calibration.md.

Usage: .venv/bin/python scripts/calibration_report.py --date 2026-06-27"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app import calibration  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.judge_render import CONDITIONS  # noqa: E402
from app.models import Criterion  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def build_report(db) -> str:
    lines = ["# VLM ↔ Human Calibration Report", ""]
    for slug in calibration.STUDY_CRITERIA:
        crit = db.execute(select(Criterion).where(Criterion.slug == slug)).scalars().first()
        if crit is None:
            continue
        lines.append(f"## Criterion: {slug}")
        lines.append("")
        lines.append("| view | κ (human vs VLM) | n | self-consistency flip-rate | rank ρ |")
        lines.append("|---|---|---|---|---|")
        for cond in CONDITIONS:
            k = calibration.human_vs_judge_kappa(db, crit.id, cond)
            sc = calibration.judge_self_consistency(db, crit.id, cond)
            rc = calibration.rank_correlation(db, crit.id, cond)
            lines.append(
                f"| {cond} | {k['kappa']} | {k['n']} | {sc['flip_rate']} | {rc['spearman']} |"
            )
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", required=True, help="YYYY-MM-DD for the output filename")
    args = ap.parse_args()
    with SessionLocal() as db:
        text = build_report(db)
    out = ROOT / "docs" / "results" / f"{args.date}-vlm-calibration.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
