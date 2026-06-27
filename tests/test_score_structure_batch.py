"""Batch OrganMetric runner — covers the iterate/tally/resume logic with a fake scorer.

The real /score_structure round-trip is the live smoke (scripts/smoke_score_structure.py);
here an injected fake scorer keeps it offline, like tests/test_structure_service.py.
"""

from __future__ import annotations

import json

from app.database import SessionLocal, init_db
from app.models import Category, Generator, ModelOutput, OrganMetric, ReconTask, Task
from app.storage import get_storage

from scripts.score_structure_batch import run_batch


def setup_module(_m):
    init_db()
    get_storage().save("seed/ssb.glb", b"glTF-stub-bytes")


def teardown_module(_m):
    # run_batch scores ALL procedural outputs in the shared DB (it is global by design),
    # so clear the OrganMetric table afterward to avoid polluting other test modules.
    with SessionLocal() as db:
        db.query(OrganMetric).delete()
        db.commit()


def _fake_card(record):
    return {
        "species": "zea_mays",
        "botanical_fidelity": 0.7,
        "n_attributes": 1,
        "attributes": {"leaf_axis_count": {"status": "PASS", "graded": 1.0}},
    }


def _mk_output(db, key, *, source, species_slug="zea_mays", variant="maize"):
    cat = Category(slug=f"ssb-c-{key}", name="Plants")
    db.add(cat)
    db.flush()
    task = Task(category_id=cat.id, title=f"ssb-t-{key}", prompt="p")
    gen = Generator(slug=f"ssb-g-{key}", name=f"M-{key}")
    db.add_all([task, gen])
    db.flush()
    if species_slug is not None:
        db.add(ReconTask(task_id=task.id, species_slug=species_slug, species_name=species_slug))
    out = ModelOutput(
        task_id=task.id,
        generator_id=gen.id,
        asset_path="seed/ssb.glb",
        asset_format="glb",
        source=source,
        meta_json=json.dumps({"variant": variant}),
    )
    db.add(out)
    db.flush()
    return out


def _clean(db):
    db.query(OrganMetric).delete()
    db.query(ModelOutput).filter(ModelOutput.asset_path == "seed/ssb.glb").delete(
        synchronize_session=False
    )
    db.query(ReconTask).filter(ReconTask.species_name == "zea_mays").delete(
        synchronize_session=False
    )
    db.query(Task).filter(Task.title.like("ssb-t-%")).delete(synchronize_session=False)
    db.query(Generator).filter(Generator.slug.like("ssb-g-%")).delete(synchronize_session=False)
    db.query(Category).filter(Category.slug.like("ssb-c-%")).delete(synchronize_session=False)
    db.commit()


def test_run_batch_scores_procedural_skips_non_procedural():
    with SessionLocal() as db:
        _clean(db)
        proc = _mk_output(db, "proc", source="procedural:agrigen")
        api = _mk_output(db, "api", source="api:hunyuan")  # non-procedural → N/A
        db.commit()

        res = run_batch(db, scorer=_fake_card)
        # The procedural structure-known output is scored; the api output is skipped (no row).
        assert res["scored"] >= 1
        assert res["skipped"] >= 1
        pm = db.query(OrganMetric).filter_by(output_id=proc.id).first()
        assert pm is not None and pm.status == "scored"
        assert abs(pm.botanical_fidelity - 0.7) < 1e-9
        assert db.query(OrganMetric).filter_by(output_id=api.id).first() is None


def test_run_batch_only_missing_skips_already_scored():
    with SessionLocal() as db:
        _clean(db)
        proc = _mk_output(db, "proc", source="procedural:agrigen")
        db.commit()
        run_batch(db, scorer=_fake_card)  # first pass scores it

        calls = []

        def counting_scorer(record):
            calls.append(1)
            return _fake_card(record)

        res = run_batch(db, scorer=counting_scorer, only_missing=True)
        # Already has an OrganMetric row → not re-scored; scorer never called.
        assert calls == []
        assert res["skipped_existing"] >= 1
        assert db.query(OrganMetric).filter_by(output_id=proc.id).count() == 1
