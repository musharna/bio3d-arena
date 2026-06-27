"""Bulk-assign difficulty tiers to recon tasks (idempotent upsert).

DIFFICULTY_MAP is keyed by ReconTask.species_slug → (tier, rationale). Slugs with no
ReconTask are skipped and logged (non-fatal). Verify slugs against the live DB:
    select(ReconTask.species_slug)

Usage: .venv/bin/python scripts/assign_difficulty.py"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.difficulty import set_task_difficulty  # noqa: E402
from app.models import ReconTask  # noqa: E402

# Editable starting curation, keyed by the live ReconTask.species_slug values.
# Tiers reflect reconstruction difficulty of the subject's geometry.
DIFFICULTY_MAP: dict[str, tuple[str, str]] = {
    "solanum_lycopersicum": ("easy", "compact bushy form, large leaves/fruit — forgiving geometry"),
    "zea_mays": ("moderate", "tall blade leaves, thin tassel/silk detail"),
    "arabidopsis_thaliana": ("hard", "fine rosette + bolting inflorescence, thin stems"),
    "pinus_sylvestris": ("hard", "dense fine needles + branch self-occlusion"),
}


def assign_all(db, mapping: dict[str, tuple[str, str]] = DIFFICULTY_MAP) -> dict:
    slug_to_task = {rt.species_slug: rt.task_id for rt in db.execute(select(ReconTask)).scalars()}
    assigned = 0
    skipped: list[str] = []
    for slug, (tier, rationale) in mapping.items():
        task_id = slug_to_task.get(slug)
        if task_id is None:
            skipped.append(slug)
            continue
        set_task_difficulty(db, task_id, tier, rationale, commit=False)
        assigned += 1
    db.commit()
    return {"assigned": assigned, "skipped": skipped}


def main() -> int:
    with SessionLocal() as db:
        res = assign_all(db)
    print(res)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
