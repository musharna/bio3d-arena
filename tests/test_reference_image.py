# tests/test_reference_image.py
import json

from app.database import SessionLocal, init_db
from app.models import Category, Generator, ModelOutput, Task
from app.service import reference_images_for_task
from app.storage import get_storage


def setup_module(_m):
    init_db()


def _clean(db):
    ids = db.query(Task.id).filter(Task.title.like("refimg-%")).scalar_subquery()
    db.query(ModelOutput).filter(ModelOutput.task_id.in_(ids)).delete(synchronize_session=False)
    db.query(Task).filter(Task.title.like("refimg-%")).delete(synchronize_session=False)
    db.query(Generator).filter(Generator.slug.like("refimg-%")).delete(synchronize_session=False)
    db.query(Category).filter_by(slug="refimg-cat").delete(synchronize_session=False)
    db.commit()


def _task_with_outputs(db, meta_list):
    cat = Category(slug="refimg-cat", name="C")
    g = Generator(slug="refimg-g", name="G")
    db.add_all([cat, g])
    db.flush()
    # title has no gallery dir on disk → references == just the input photo(s)
    t = Task(category_id=cat.id, title="refimg-Nonesuch fictus — recon", prompt="p")
    db.add(t)
    db.flush()
    for i, meta in enumerate(meta_list):
        db.add(
            ModelOutput(
                task_id=t.id, generator_id=g.id, asset_path=f"refimg/{i}.glb", meta_json=meta
            )
        )
    db.commit()
    return t


def test_reference_list_includes_input_photo_with_credit():
    with SessionLocal() as db:
        _clean(db)
        t = _task_with_outputs(
            db,
            ['{"modality": "text"}', json.dumps({"input_image": "reference/puffball_ref.jpg"})],
        )
        refs = reference_images_for_task(db, t)
        assert refs == [
            {"url": get_storage().url_for("reference/puffball_ref.jpg"), "credit": "reconstruction input photo"}
        ]
        _clean(db)


def test_reference_empty_when_no_input_image_and_no_gallery():
    with SessionLocal() as db:
        _clean(db)
        t = _task_with_outputs(db, ["{}", '{"provider": "x"}'])
        assert reference_images_for_task(db, t) == []
        _clean(db)


def test_reference_dedups_and_survives_bad_meta_json():
    with SessionLocal() as db:
        _clean(db)
        # a malformed meta row must not crash; the same input twice appears once (dedup)
        t = _task_with_outputs(
            db,
            [
                "not-json",
                json.dumps({"input_image": "reference/rose_ref.jpg"}),
                json.dumps({"input_image": "reference/rose_ref.jpg"}),
            ],
        )
        refs = reference_images_for_task(db, t)
        assert [r["url"] for r in refs] == [get_storage().url_for("reference/rose_ref.jpg")]
        _clean(db)
