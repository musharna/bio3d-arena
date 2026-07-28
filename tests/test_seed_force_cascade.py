"""seed_all(force=True) must wipe child rows before their parents, or a force reseed strands
them -> SQLite rowid reuse -> UNIQUE collision / stale association. This guards against the
delete-list drifting out of sync with the schema again.

The original version of this file checked ONE parent (model_output) because that was the
table the known bug orphaned. That scope was the hole: the list also wipes generator, task,
criterion and category, and nothing checked THEIR children. Turning on FK enforcement found
five tables the list never deleted (judge_rating, kingdom_rating, kingdom_judge_rating,
task_difficulty, trait_rubric) and one edge it deleted in the wrong order. The coverage check
below is now parent-agnostic, and the behavioural test underneath it runs the real reseed
against real enforcement so a future gap fails loudly instead of silently orphaning.
"""

from __future__ import annotations

import datetime as dt
import random

from sqlalchemy import text

from app import seed as seed_mod
from app.database import Base, SessionLocal, engine, init_db
from app.models import (
    Category,
    Criterion,
    Generator,
    JudgeRating,
    KingdomJudgeRating,
    KingdomRating,
    Task,
    TaskDifficulty,
    TraitRubric,
)


def setup_module(_m):
    init_db()


def _wiped_tables() -> dict[str, int]:
    """Table name -> position in the wipe order."""
    return {m.__tablename__: i for i, m in enumerate(seed_mod._FORCE_DELETE_MODELS)}


def test_force_delete_list_covers_children_of_every_wiped_parent():
    """Every table holding an FK into a table the reseed wipes must itself be wiped.

    Parent-agnostic on purpose: scoping this to model_output is what let five tables sit
    uncovered while the test stayed green.
    """
    wiped = _wiped_tables()
    missing = set()
    for table in Base.metadata.tables.values():
        for fk in table.foreign_keys:
            if fk.column.table.name in wiped and table.name not in wiped:
                missing.add((table.name, fk.column.table.name))
    assert not missing, (
        "seed_all(force=True) wipes these parents but never deletes their children, "
        f"orphaning them: {sorted(missing)}"
    )


def test_force_reseed_leaves_the_database_referentially_intact():
    """Real-execution check: populate the tables the list forgot, run the ACTUAL reseed with
    FK enforcement live, and assert SQLite itself finds no dangling references afterwards.

    A structural test over the model metadata can only catch what it thinks to look for.
    This one asks the database.
    """
    tag = random.randint(0, 10**6)
    now = dt.datetime.now(dt.timezone.utc)
    db = SessionLocal()
    try:
        cat = Category(slug=f"c-sfc-{tag}", name="Plants")
        db.add(cat)
        db.flush()
        task = Task(category_id=cat.id, title=f"SFC {tag}", prompt="p")
        gen = Generator(slug=f"g-sfc-{tag}", name="SFCGen")
        crit = db.query(Criterion).filter_by(slug="overall").first()
        if crit is None:
            crit = Criterion(slug="overall", name="Overall")
            db.add(crit)
        db.add_all([task, gen])
        db.flush()

        bt = dict(bt_score=0.0, bt_lower=-1.0, bt_upper=1.0, n_games=1, updated=now)
        db.add_all(
            [
                JudgeRating(
                    generator_id=gen.id,
                    criterion_id=crit.id,
                    view_condition="multi4",
                    elo=1000.0,
                    judge_model="m",
                    **bt,
                ),
                KingdomRating(generator_id=gen.id, kingdom="plants", criterion_id=crit.id, **bt),
                KingdomJudgeRating(
                    generator_id=gen.id,
                    kingdom="plants",
                    criterion_id=crit.id,
                    view_condition="multi4",
                    **bt,
                ),
                TaskDifficulty(task_id=task.id, tier="easy", rationale="r", updated=now),
                TraitRubric(task_id=task.id, taxon="x", traits_json="{}", created=now, updated=now),
            ]
        )
        db.commit()
    finally:
        db.close()

    seed_mod.seed_all(force=True)  # raises today: FOREIGN KEY constraint failed

    with engine.connect() as conn:
        dangling = conn.execute(text("PRAGMA foreign_key_check")).fetchall()
    assert dangling == [], f"reseed left dangling references: {dangling}"
