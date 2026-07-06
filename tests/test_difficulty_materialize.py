# tests/test_difficulty_materialize.py
import json

import pytest
from sqlalchemy import select

from app import difficulty
from app.database import SessionLocal, init_db
from app.models import Category, ReconTask, Task, TaskDifficulty, TaxonDifficulty


def setup_module(_m):
    init_db()


def _mk_task(db, title, cat):
    t = Task(category_id=cat.id, title=title, prompt="p")
    db.add(t)
    db.flush()
    return t


def _seed(db, prefix):
    """flush-only fixture (rolls back on close). Unique category slug per test."""
    cat = Category(slug=f"{prefix}-cat", name="Plants")
    db.add(cat)
    db.flush()
    t_recon = _mk_task(db, "Rosa — single-image → 3D reconstruction", cat)
    t_bot = _mk_task(db, "Rosa — botanical plausibility", cat)
    t_zea = _mk_task(db, "Zea mays — single-image → 3D reconstruction", cat)
    db.add(ReconTask(task_id=t_recon.id, species_slug="rosa", species_name="Rose"))
    db.add(
        TaxonDifficulty(
            species_slug="rosa", tier="hard", axis_scores=json.dumps({}), rationale=json.dumps({})
        )
    )
    db.add(
        TaxonDifficulty(
            species_slug="zea_mays",
            tier="moderate",
            axis_scores=json.dumps({}),
            rationale=json.dumps({}),
        )
    )
    db.flush()
    return t_recon, t_bot, t_zea


def test_species_slug_for_task_parses_binomial():
    with SessionLocal() as db:
        t_recon, t_bot, t_zea = _seed(db, "dmp1")
        assert difficulty.species_slug_for_task(t_recon) == "rosa"
        assert difficulty.species_slug_for_task(t_bot) == "rosa"
        assert difficulty.species_slug_for_task(t_zea) == "zea_mays"


def test_species_slug_matches_recon_task():
    with SessionLocal() as db:
        t_recon, _, _ = _seed(db, "dmp2")
        rt = db.execute(select(ReconTask).where(ReconTask.task_id == t_recon.id)).scalars().one()
        assert difficulty.species_slug_for_task(t_recon) == rt.species_slug


def test_species_slug_fails_loud_on_empty_title():
    with SessionLocal() as db:
        cat = Category(slug="dmp3-cat", name="X")
        db.add(cat)
        db.flush()
        t = _mk_task(db, "— nothing before the dash", cat)
        with pytest.raises(ValueError):
            difficulty.species_slug_for_task(t)


def test_materialize_projects_taxon_onto_all_tasks():
    with SessionLocal() as db:
        t_recon, t_bot, t_zea = _seed(db, "dmp4")
        res = difficulty.materialize_task_difficulty(db, commit=False)
        covered = {t_recon.id, t_bot.id, t_zea.id}
        by_task = {
            td.task_id: td.tier
            for td in db.execute(select(TaskDifficulty)).scalars()
            if td.task_id in covered
        }
        assert by_task[t_recon.id] == "hard"
        assert by_task[t_bot.id] == "hard"  # second rosa task inherits the SAME tier
        assert by_task[t_zea.id] == "moderate"
        assert res["materialized"] >= 3  # my 3 (+ any leaked covered tasks)
        # idempotent: re-run, still exactly one TaskDifficulty row per covered task
        difficulty.materialize_task_difficulty(db, commit=False)
        for tid in covered:
            rows = [td for td in db.execute(select(TaskDifficulty)).scalars() if td.task_id == tid]
            assert len(rows) == 1


def test_materialize_reports_uncovered_in_skipped():
    with SessionLocal() as db:
        cat = Category(slug="dmp5-cat", name="Plants")
        db.add(cat)
        db.flush()
        t = _mk_task(
            db, "Cucumis sativus — single-image → 3D reconstruction", cat
        )  # no TaxonDifficulty
        res = difficulty.materialize_task_difficulty(db, commit=False)
        assert (t.id, "cucumis_sativus") in res["skipped"]
