# tests/test_seed_completeness_rubrics.py
from app.completeness import enumerate_completeness_work
from app.database import SessionLocal, init_db
from app.models import Category, Generator, ModelOutput, Task, TraitRubric
from scripts.seed_completeness_rubrics import seed_scope_rubrics, taxon_for_task_title


def setup_module(_m):
    init_db()


def _clean(db):
    seedtest_task_ids = db.query(Task.id).filter(Task.title.like("%SEEDTEST%")).scalar_subquery()
    db.query(TraitRubric).filter(TraitRubric.task_id.in_(seedtest_task_ids)).delete(
        synchronize_session=False
    )
    db.query(ModelOutput).filter(ModelOutput.asset_path.like("scr/%.glb")).delete(
        synchronize_session=False
    )
    db.query(Task).filter(Task.title.like("%SEEDTEST%")).delete(synchronize_session=False)
    db.query(Generator).filter(Generator.slug == "scr-gen").delete(synchronize_session=False)
    db.query(Category).filter_by(slug="scr-cat").delete(synchronize_session=False)
    db.commit()


def test_taxon_for_task_title():
    assert (
        taxon_for_task_title("Lycoperdon perlatum — single-image → 3D reconstruction")
        == "Lycoperdon perlatum"
    )


def test_seeds_only_inventory_covered_taxa_and_is_idempotent():
    with SessionLocal() as db:
        _clean(db)
        cat = Category(slug="scr-cat", name="C")
        db.add(cat)
        db.flush()
        # one fungus WITH an inventory, one made-up taxon WITHOUT one (real title shape: em dash)
        fungus = Task(category_id=cat.id, title="Lycoperdon perlatum — SEEDTEST recon", prompt="p")
        nope = Task(category_id=cat.id, title="Nonexistus fictus — SEEDTEST recon", prompt="p")
        db.add_all([fungus, nope])
        db.flush()

        summary = seed_scope_rubrics(db, [fungus.id, nope.id])
        db.commit()
        assert (fungus.id, "Lycoperdon perlatum") in summary["seeded"]
        assert any(t == nope.id for t, _ in summary["skipped_no_inventory"])

        # a rubric now exists for the fungus, and completeness enumerate can reach its outputs
        gen = Generator(slug="scr-gen", name="G", paradigm="image_recon")
        db.add(gen)
        db.flush()
        db.add(ModelOutput(task_id=fungus.id, generator_id=gen.id, asset_path="scr/1.glb"))
        db.commit()
        work = enumerate_completeness_work(db, [fungus.id])
        assert len(work) == 1 and work[0]["taxon"] == "Lycoperdon perlatum"

        # idempotent: re-seed creates nothing new
        again = seed_scope_rubrics(db, [fungus.id])
        db.commit()
        assert again["seeded"] == []
        assert (fungus.id, "Lycoperdon perlatum") in again["existing"]
        _clean(db)
