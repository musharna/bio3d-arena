"""Generate the difficulty-tier scorecard → docs/results/<date>-difficulty-scorecard.md.

Usage: .venv/bin/python scripts/difficulty_report.py --date 2026-06-27"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import difficulty  # noqa: E402
from app.database import SessionLocal  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def _fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:.3f}"


def build_report(db) -> str:
    lines = ["# Difficulty-Tier Scorecard", ""]
    for card in difficulty.tier_scorecard(db):
        lines.append(f"## Tier: {card['tier']}")
        lines.append("")
        lines.append(
            "| generator | n | scored | mean chamfer↓ | mean F-score↑ | "
            "mean structural↑ | species PASS-rate↑ |"
        )
        lines.append("|---|---|---|---|---|---|---|")
        if not card["rows"]:
            lines.append("| _(no tasks in this tier)_ | | | | | | |")
        for r in card["rows"]:
            lines.append(
                f"| {r['generator']} | {r['n_outputs']} | {r['n_scored']} | "
                f"{_fmt(r['mean_chamfer'])} | {_fmt(r['mean_fscore'])} | "
                f"{_fmt(r['mean_structural'])} | {_fmt(r['species_pass_rate'])} |"
            )
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", required=True, help="YYYY-MM-DD for the output filename")
    args = ap.parse_args()
    with SessionLocal() as db:
        text = build_report(db)
    out = ROOT / "docs" / "results" / f"{args.date}-difficulty-scorecard.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
