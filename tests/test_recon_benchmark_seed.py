"""B5-prep: the recon benchmark scaffolding — 4 species Tasks with a Task→species-slug
mapping (ReconTask) + the 2 method Generators, all idempotent so the moment GLBs land,
ingest + /admin/rescore resolves the GT-bundle slug and just works."""

from __future__ import annotations

from app import recon_service, seed
from app.database import SessionLocal, init_db
from app.models import Generator, ReconTask, Task


def setup_module(_module):
    init_db()


EXPECTED_SLUGS = {
    "arabidopsis_thaliana",
    "solanum_lycopersicum",
    "zea_mays",
    "pinus_sylvestris",
}


def test_seed_recon_benchmark_creates_tasks_slugs_generators():
    db = SessionLocal()
    try:
        seed.seed_recon_benchmark(db)
        db.commit()
        slugs = {rt.species_slug for rt in db.query(ReconTask).all()}
        assert EXPECTED_SLUGS <= slugs
        # the 2 method generators exist
        gen_slugs = {g.slug for g in db.query(Generator).all()}
        assert {"trellis", "hunyuan3d"} <= gen_slugs
        # every ReconTask points at a real Task
        for rt in db.query(ReconTask).all():
            assert db.get(Task, rt.task_id) is not None
    finally:
        db.close()


def test_seed_recon_benchmark_is_idempotent():
    db = SessionLocal()
    try:
        seed.seed_recon_benchmark(db)
        db.commit()
        n_tasks = db.query(ReconTask).count()
        n_gens = db.query(Generator).filter(Generator.slug.in_(["trellis", "hunyuan3d"])).count()
        seed.seed_recon_benchmark(db)  # re-run
        db.commit()
        assert db.query(ReconTask).count() == n_tasks  # no duplicates
        assert (
            db.query(Generator).filter(Generator.slug.in_(["trellis", "hunyuan3d"])).count()
            == n_gens
        )
    finally:
        db.close()


def test_species_slug_resolves_for_a_recon_task():
    db = SessionLocal()
    try:
        seed.seed_recon_benchmark(db)
        db.commit()
        rt = db.query(ReconTask).filter(ReconTask.species_slug == "zea_mays").first()
        assert recon_service.species_slug_for_task(db, rt.task_id) == "zea_mays"
        # a non-recon task resolves to None
        from app.models import Category

        cat = Category(slug="c-nonrecon", name="X")
        db.add(cat)
        db.flush()
        plain = Task(category_id=cat.id, title="plain", prompt="p")
        db.add(plain)
        db.flush()
        assert recon_service.species_slug_for_task(db, plain.id) is None
    finally:
        db.close()
