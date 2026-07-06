# scripts/score_semantic.py
"""Batch-score semantic admissibility for outputs. Renders (or reuses) a turntable contact sheet
per output, runs the semantic VLM judge, persists an Admissibility(predicate='semantic') row.
Persistence is unconditional; advisory flags are emitted only when the configured mode is
'advisory'. Build the Anthropic client from ANTHROPIC_API_KEY (as scripts/judge_vlm.py does).

NEVER set BIO3D_DATABASE_URL=study. For the acceptance run, point BIO3D_DATABASE_URL at a COPY of
the study DB and set BIO3D_SEMANTIC_ADMISSIBILITY_MODE=off (persist verdicts, no advisory flags)."""

from __future__ import annotations

import argparse
import os
import sys

# bootstrap: allow `python scripts/<name>.py` without PYTHONPATH (repo root on sys.path)
import sys as _sys
import pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parent.parent))

from app import config
from app.database import SessionLocal, init_db
from app.judge_render import contact_sheet_path, render_contact_sheets
from app.semantic import enumerate_semantic_work, evaluate_outputs

CONDITION = "turntable"  # same as completeness -> cached sheets are reused


def _sheet_provider(db, capture_multi):
    """Render (idempotently) then read the turntable contact-sheet PNG bytes for an output."""

    def sheet_for(output_id: int) -> bytes:
        render_contact_sheets(db, [output_id], CONDITION, capture_multi=capture_multi)
        path = os.path.join(config.ASSET_DIR, contact_sheet_path(output_id, CONDITION))
        with open(path, "rb") as f:
            return f.read()

    return sheet_for


def _build_client():
    import anthropic

    return anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def _capture_multi():
    from scripts.judge_capture import browser_capture_multi_factory

    return browser_capture_multi_factory()


def main() -> int:
    ap = argparse.ArgumentParser(description="Batch-score semantic admissibility.")
    ap.add_argument("--limit", type=int, default=0, help="score at most N outputs (0 = all)")
    args = ap.parse_args()
    init_db()
    with SessionLocal() as db:
        work = enumerate_semantic_work(db)
        if args.limit:
            work = work[: args.limit]
        emit_flags = config.SEMANTIC_ADMISSIBILITY_MODE == "advisory"
        sheet_for = _sheet_provider(db, _capture_multi())
        summary = evaluate_outputs(
            db, work, client=_build_client(), sheet_for=sheet_for, emit_flags=emit_flags
        )
        db.commit()
    print({"mode": config.SEMANTIC_ADMISSIBILITY_MODE, **summary})
    return 0 if summary["errors"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
