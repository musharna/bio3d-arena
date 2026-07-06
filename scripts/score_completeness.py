# scripts/score_completeness.py
"""Batch-score organism-level completeness for outputs. Renders (or reuses) a turntable contact
sheet per output, VLM-checks organ presence, persists a Completeness row. Build the Anthropic
client from ANTHROPIC_API_KEY (as scripts/judge_vlm.py does). Never set BIO3D_DATABASE_URL=study.

NOTE: the plan/brief called this condition "multi8"; app.judge_render.CONDITIONS has no such key
(only "single", "multi4", "turntable" — verified live, 2026-07-01). "turntable" is the closest
existing match (8 azimuths, i.e. an actual multi-8-view sheet) and is used here instead of
inventing a new condition, per the "reuse existing infra" constraint."""

from __future__ import annotations

import argparse
import sys

# bootstrap: allow `python scripts/<name>.py` without PYTHONPATH (repo root on sys.path)
import sys as _sys
import pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parent.parent))

from app.database import SessionLocal, init_db
from app.completeness import enumerate_completeness_work, score_outputs
from app.judge_render import contact_sheet_path, render_contact_sheets
from app import config

SCORER_VERSION = "completeness-v1"
CONDITION = "turntable"


def _sheet_provider(db, capture_multi):
    """Render (idempotently) then read the turntable contact-sheet PNG bytes for an output."""
    import os

    def sheet_for(output_id: int) -> bytes:
        render_contact_sheets(db, [output_id], CONDITION, capture_multi=capture_multi)
        path = os.path.join(config.ASSET_DIR, contact_sheet_path(output_id, CONDITION))
        with open(path, "rb") as f:
            return f.read()

    return sheet_for


def _build_client():
    import os
    import anthropic

    return anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def _capture_multi():
    # The Playwright multi-angle GLB capture used by the judge pipeline. Import lazily so
    # unit tests never need a browser. Reuse the same capture the judge batch uses.
    from scripts.judge_capture import browser_capture_multi_factory

    return browser_capture_multi_factory()


def main() -> int:
    ap = argparse.ArgumentParser(description="Batch-score organism-level completeness.")
    ap.add_argument("--tasks", default="", help="comma task ids (default: all with a rubric)")
    args = ap.parse_args()
    init_db()
    with SessionLocal() as db:
        from app.models import Task, TraitRubric

        if args.tasks:
            task_ids = sorted(set(int(x) for x in args.tasks.split(",") if x.strip()))
        else:
            task_ids = [
                t.id for t in db.query(Task).join(TraitRubric, TraitRubric.task_id == Task.id)
            ]
        work = enumerate_completeness_work(db, task_ids)
        sheet_for = _sheet_provider(db, _capture_multi())
        summary = score_outputs(
            db, work, client=_build_client(), sheet_for=sheet_for, scorer_version=SCORER_VERSION
        )
        db.commit()
    print(summary)
    return 0 if summary["errors"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
