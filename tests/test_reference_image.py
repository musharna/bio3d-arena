# tests/test_reference_image.py
import json

from app.database import SessionLocal, init_db
from app.models import Category, Generator, ModelOutput, Task
from app.service import reference_image_for_task
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
    t = Task(category_id=cat.id, title="refimg-t", prompt="p")
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


def test_reference_url_from_output_input_image():
    with SessionLocal() as db:
        _clean(db)
        # a text output (no input image) + a recon output that recorded its input photo
        t = _task_with_outputs(
            db,
            ['{"modality": "text"}', json.dumps({"input_image": "reference/puffball_ref.jpg"})],
        )
        url = reference_image_for_task(db, t)
        assert url == get_storage().url_for("reference/puffball_ref.jpg")
        _clean(db)


def test_reference_none_when_no_input_image():
    with SessionLocal() as db:
        _clean(db)
        t = _task_with_outputs(db, ["{}", '{"provider": "x"}'])
        assert reference_image_for_task(db, t) is None
        _clean(db)


def test_reference_survives_bad_meta_json():
    with SessionLocal() as db:
        _clean(db)
        # a malformed meta row must not crash the lookup; the good one still resolves
        t = _task_with_outputs(
            db, ["not-json", json.dumps({"input_image": "reference/rose_ref.jpg"})]
        )
        assert reference_image_for_task(db, t) == get_storage().url_for("reference/rose_ref.jpg")
        _clean(db)
