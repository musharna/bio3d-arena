# scripts/seed_completeness_rubrics.py
"""Ensure a minimal TraitRubric exists for every task whose taxon has an authored organ
inventory, so the organism-level completeness scorer (which gates on TraitRubric existence,
enumerate_completeness_work) can reach it. This is the completeness SCOPE gate, not the Mode-C
morphology rubric: the row carries only (task_id, taxon) — traits_json stays '[]' (completeness
never reads it). Idempotent: an existing rubric for a task is left untouched. init_db() first so
a study DB predating any new column self-heals. Run on a study COPY, then promote.

Taxon = the binomial before the em dash in the task title (matches ORGAN_INVENTORY keys). A task
whose taxon has no inventory is skipped and reported — completeness does not apply to it."""

from __future__ import annotations

import argparse
import sys

# bootstrap: allow `python scripts/<name>.py` without PYTHONPATH (repo root on sys.path)
import sys as _sys
import pathlib as _pl

_sys.path.insert(0, str(_pl.Path(__file__).resolve().parent.parent))

from app.database import SessionLocal, init_db
from app.organ_inventory import inventory_for


def taxon_for_task_title(title: str) -> str:
    """'Lycoperdon perlatum — single-image → 3D reconstruction' -> 'Lycoperdon perlatum'."""
    return title.split("—")[0].strip()


def seed_scope_rubrics(db, task_ids: list[int]) -> dict:
    """For each task id: resolve its taxon, and if that taxon has an organ inventory but the task
    has no TraitRubric yet, create a minimal one. Returns a skip-and-report summary. Caller commits."""
    from app.models import Task, TraitRubric

    seeded, existing, skipped_no_inventory, missing = [], [], [], []
    for tid in task_ids:
        task = db.get(Task, tid)
        if task is None:
            missing.append(tid)
            continue
        taxon = taxon_for_task_title(task.title)
        if inventory_for(taxon) is None:
            skipped_no_inventory.append((tid, taxon))
            continue
        if db.query(TraitRubric).filter_by(task_id=tid).first() is not None:
            existing.append((tid, taxon))
            continue
        db.add(TraitRubric(task_id=tid, taxon=taxon))  # traits_json defaults to '[]'
        seeded.append((tid, taxon))
    return {
        "seeded": seeded,
        "existing": existing,
        "skipped_no_inventory": skipped_no_inventory,
        "missing_task": missing,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Seed minimal completeness-scope TraitRubrics.")
    ap.add_argument(
        "--tasks",
        default="",
        help="comma task ids (default: all tasks whose taxon has an organ inventory)",
    )
    args = ap.parse_args()
    init_db()
    with SessionLocal() as db:
        from app.models import Task

        if args.tasks:
            task_ids = sorted({int(x) for x in args.tasks.split(",") if x.strip()})
        else:
            # default: every task whose title-binomial resolves to an authored inventory
            task_ids = [
                t.id
                for t in db.query(Task).all()
                if inventory_for(taxon_for_task_title(t.title)) is not None
            ]
        summary = seed_scope_rubrics(db, task_ids)
        db.commit()
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
