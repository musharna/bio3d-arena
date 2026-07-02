# scripts/validate_completeness.py
"""Validate the completeness metric: derive GT categories from the trait-level calibration
CSVs, compare against persisted Completeness rows, report binary+4-way kappa and isolated
recall, and print the auditable GT mapping. Writes docs/results/2026-07-01-completeness-
validation-results.md. Never set BIO3D_DATABASE_URL=study."""

from __future__ import annotations

import csv
import glob
import sys

from app.database import SessionLocal, init_db
from app.completeness_validation import gt_by_output, agreement, map_note_to_category


def _load_calibration_rows() -> list[dict]:
    rows = []
    for path in glob.glob("data/study/calibration_labels*.csv"):
        with open(path, newline="") as f:
            for r in csv.DictReader(f):
                if "output_id" not in r:
                    continue
                try:
                    r["output_id"] = int(r["output_id"])
                except (TypeError, ValueError):
                    continue
                rows.append(r)
    return rows


def main() -> int:
    init_db()
    cal = _load_calibration_rows()
    gt = gt_by_output(cal)
    with SessionLocal() as db:
        from app.models import Completeness

        pred = {c.output_id: c.category for c in db.query(Completeness).all()}
    m = agreement(pred, gt)
    lines = [
        "# Completeness metric — validation results",
        "",
        f"- eval outputs (in both GT and pred): {m['n']}",
        f"- binary complete/incomplete kappa: {m['binary_kappa']}",
        f"- 4-way category kappa: {m['fourway_kappa']}",
        f"- isolated-organ recall: {m['isolated_recall']}",
        f"- GT outputs with no prediction (dropped): {m['dropped']}",
        "",
        "## Auditable GT mapping (note -> category)",
    ]
    for r in cal[:200]:
        cat = map_note_to_category(r.get("human_verdict", ""), r.get("note", ""))
        lines.append(f"- output {r['output_id']}: {(r.get('note') or '')[:60]!r} -> {cat}")
    with open("docs/results/2026-07-01-completeness-validation-results.md", "w") as f:
        f.write("\n".join(lines) + "\n")
    print(m)
    # PASS gate: binary kappa >= 0.6 (4-way may be experimental).
    bk = m["binary_kappa"]
    return 0 if (bk is not None and bk >= 0.6) else 2


if __name__ == "__main__":
    sys.exit(main())
