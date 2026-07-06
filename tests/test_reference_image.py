# tests/test_reference_image.py
import json

from app.database import SessionLocal, init_db
from app.models import Category, Generator, ModelOutput, Task
from app.service import reference_images_for_task
from app.storage import get_storage


def setup_module(_m):
    init_db()


def _clean(db):
    # Key cleanup off the stable category/generator slugs, not the task title — these tests use a
    # barley-exempt task title (see below) so title-prefix matching no longer identifies them.
    cat = db.query(Category).filter_by(slug="refimg-cat").first()
    if cat is not None:
        ids = db.query(Task.id).filter(Task.category_id == cat.id).scalar_subquery()
        db.query(ModelOutput).filter(ModelOutput.task_id.in_(ids)).delete(synchronize_session=False)
        db.query(Task).filter(Task.category_id == cat.id).delete(synchronize_session=False)
    db.query(Generator).filter(Generator.slug.like("refimg-%")).delete(synchronize_session=False)
    db.query(Category).filter_by(slug="refimg-cat").delete(synchronize_session=False)
    db.commit()


def _task_with_outputs(db, meta_list):
    cat = Category(slug="refimg-cat", name="C")
    g = Generator(slug="refimg-g", name="G")
    db.add_all([cat, g])
    db.flush()
    # Recon INPUT photos are surfaced as references ONLY for INPUT_REFERENCE_EXEMPT_SLUGS
    # (barley-MRI). Non-exempt tasks exclude the input entirely (that path is covered by
    # tests/test_reference_exclusion.py). Use the barley-exempt slug here so the input-photo
    # logic (cleared-taxa filter, hidden_at exclusion, dedup, bad-meta survival) is still exercised.
    t = Task(category_id=cat.id, title="Hordeum vulgare — refimg recon", prompt="p")
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


def test_reference_list_includes_input_photo_with_credit(monkeypatch):
    from app import reference_provenance

    monkeypatch.setattr(reference_provenance, "cleared_reference_taxa", lambda: {"puffball"})
    with SessionLocal() as db:
        _clean(db)
        t = _task_with_outputs(
            db,
            ['{"modality": "text"}', json.dumps({"input_image": "reference/puffball_ref.jpg"})],
        )
        refs = reference_images_for_task(db, t)
        assert refs == [
            {
                "url": get_storage().url_for("reference/puffball_ref.jpg"),
                "credit": "reconstruction input photo",
            }
        ]
        _clean(db)


def test_reference_empty_when_no_input_image_and_no_gallery():
    with SessionLocal() as db:
        _clean(db)
        t = _task_with_outputs(db, ["{}", '{"provider": "x"}'])
        assert reference_images_for_task(db, t) == []
        _clean(db)


def test_reference_excludes_hidden_output_input_photo(monkeypatch):
    """A withdrawn (hidden_at) output must not surface its input photo — it may be a
    since-replaced or non-redistributable (non-CC) source that must not appear in the UI."""
    import datetime as dt

    from app import reference_provenance

    # both tomato inputs are cleared here — this test's point is the hidden_at exclusion,
    # not the CC-clearance filter (that's covered by test_reference_suppresses_uncleared_input_photo).
    monkeypatch.setattr(reference_provenance, "cleared_reference_taxa", lambda: {"tomato"})

    with SessionLocal() as db:
        _clean(db)
        t = _task_with_outputs(
            db,
            [
                json.dumps({"input_image": "reference/tomato_ref_clean.jpg"}),
                json.dumps({"input_image": "reference/tomato_ref_roma.jpg"}),  # non-CC, to hide
            ],
        )
        outs = (
            db.query(ModelOutput).filter(ModelOutput.task_id == t.id).order_by(ModelOutput.id).all()
        )
        outs[1].hidden_at = dt.datetime(2026, 7, 5, tzinfo=dt.timezone.utc)
        db.commit()

        refs = reference_images_for_task(db, t)
        urls = [r["url"] for r in refs]
        assert get_storage().url_for("reference/tomato_ref_clean.jpg") in urls
        assert get_storage().url_for("reference/tomato_ref_roma.jpg") not in urls
        _clean(db)


def test_reference_dedups_and_survives_bad_meta_json(monkeypatch):
    from app import reference_provenance

    monkeypatch.setattr(reference_provenance, "cleared_reference_taxa", lambda: {"rose"})
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


def test_reference_suppresses_uncleared_input_photo(monkeypatch):
    # A visible recon output whose input photo taxon is NOT cleared must not surface the photo;
    # a cleared input still shows. (Gallery-less task title → refs == the shown input photos.)
    import json
    from app import reference_provenance
    from app.service import reference_images_for_task
    from app.storage import get_storage

    # only 'tomato' is cleared; 'rose' is not
    monkeypatch.setattr(reference_provenance, "cleared_reference_taxa", lambda: {"tomato"})

    with SessionLocal() as db:
        _clean(db)
        t = _task_with_outputs(
            db,
            [
                json.dumps({"input_image": "reference/tomato_ref_clean.jpg"}),  # cleared → shown
                json.dumps({"input_image": "reference/rose_ref.jpg"}),  # uncleared → suppressed
            ],
        )
        refs = reference_images_for_task(db, t)
        urls = [r["url"] for r in refs]
        assert get_storage().url_for("reference/tomato_ref_clean.jpg") in urls
        assert get_storage().url_for("reference/rose_ref.jpg") not in urls
        _clean(db)
