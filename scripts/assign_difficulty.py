"""Seed TaxonDifficulty from difficulty_rubric.RUBRIC, then materialize per-task
TaskDifficulty rows. Idempotent. Refuses to run against a non-copy (study/prod) DB, and
fail-loud if any task's species has no rubric coverage.

Usage: .venv/bin/python scripts/assign_difficulty.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app import config, difficulty  # noqa: E402
from app.database import SessionLocal, init_db  # noqa: E402
from app.difficulty_rubric import RUBRIC, tier_for_scores  # noqa: E402
from app.models import TaxonDifficulty  # noqa: E402


def seed_taxon_difficulty(db, rubric: dict | None = None, commit: bool = True) -> dict:
    """Upsert one TaxonDifficulty row per taxon in the rubric. Fail-loud on bad scores
    (via tier_for_scores). Idempotent (upsert by species_slug). commit=False for tests."""
    rubric = RUBRIC if rubric is None else rubric
    seeded = 0
    for slug, entry in rubric.items():
        tier = tier_for_scores(entry["scores"])
        row = (
            db.execute(select(TaxonDifficulty).where(TaxonDifficulty.species_slug == slug))
            .scalars()
            .first()
        )
        if row is None:
            row = TaxonDifficulty(species_slug=slug)
            db.add(row)
        row.tier = tier
        row.axis_scores = json.dumps(entry["scores"])
        row.rationale = json.dumps(entry["rationale"])
        seeded += 1
    # Explicit flush (SessionLocal is autoflush=False — see app/difficulty.py:
    # materialize_task_difficulty for the same pattern): makes the rows added above visible
    # to callers querying TaxonDifficulty in this same uncommitted session/transaction
    # (tests with commit=False; a re-run for idempotency), without ending the transaction
    # the way an actual commit would.
    db.flush()
    if commit:
        db.commit()
    return {"seeded": seeded}


def main() -> int:
    if not config.is_safe_test_db_target(config.DATABASE_URL):
        # Seeding mutates TaxonDifficulty/TaskDifficulty. Never run against the real study DB;
        # point BIO3D_DATABASE_URL at a copy.
        raise SystemExit(
            "refusing to run against a non-copy DB — is_safe_test_db_target False; use a copy"
        )
    # Ensure the schema exists: a pre-existing DB copy predates the taxon_difficulty table,
    # and create_all only adds missing tables (never drops/wipes). Mirrors app boot.
    init_db()
    with SessionLocal() as db:
        seed = seed_taxon_difficulty(db)
        result = difficulty.materialize_task_difficulty(db)
    if result["skipped"]:
        # fail-loud at the operational boundary: a task with no rubric coverage.
        raise SystemExit(
            f"uncovered tasks (no TaxonDifficulty for their species): {result['skipped']}"
        )
    print({**seed, "materialized": result["materialized"], "taxa": result["taxa"]})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
