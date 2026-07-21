import json
import uuid

from app import config, service
from app.database import SessionLocal, init_db
from app.models import Category, Generator, ModelOutput, Task


def setup_module(_m):
    init_db()


def _task_with_input(db, title, input_image):
    tag = uuid.uuid4().hex[:8]
    cat = Category(slug=f"rex-{tag}", name="c")
    g = Generator(slug=f"rex-g-{tag}", name="g", kind="model", paradigm="image_recon")
    db.add_all([cat, g])
    db.flush()
    t = Task(category_id=cat.id, title=title, prompt="p", active=True)
    db.add(t)
    db.flush()
    o = ModelOutput(
        task_id=t.id,
        generator_id=g.id,
        asset_path="a.glb",
        source="bio3d-arena",
        meta_json=json.dumps({"input_image": input_image}),
    )
    db.add(o)
    db.flush()
    return t


def test_non_exempt_task_drops_input_photo(monkeypatch):
    monkeypatch.setattr(
        "app.reference_provenance.cleared_reference_images", lambda: {"tomato_ref.jpg"}
    )
    with SessionLocal() as db:
        t = _task_with_input(
            db,
            "Solanum lycopersicum — single-image → 3D reconstruction",
            "reference/tomato_ref.jpg",
        )
        refs = service.reference_images_for_task(db, t)
        assert not any("reconstruction input photo" == r["credit"] for r in refs)
        assert not any("tomato_ref.jpg" in r["url"] for r in refs)
        db.rollback()


def test_barley_mri_task_keeps_input_photo(monkeypatch):
    monkeypatch.setattr(
        "app.reference_provenance.cleared_reference_images", lambda: {"hordeum_ref.jpg"}
    )
    assert "hordeum_vulgare" in config.INPUT_REFERENCE_EXEMPT_SLUGS
    with SessionLocal() as db:
        t = _task_with_input(
            db, "Hordeum vulgare — barley root system (3D MRI)", "reference/hordeum_ref.jpg"
        )
        refs = service.reference_images_for_task(db, t)
        assert any("hordeum_ref.jpg" in r["url"] for r in refs)  # exempt: input retained
        db.rollback()


def test_qa_failed_gallery_item_not_shown(monkeypatch, tmp_path):
    # Point ASSET_DIR at a temp tree with a manifest carrying one passed + one failed item.
    monkeypatch.setattr(config, "ASSET_DIR", tmp_path)
    slug = "solanum_lycopersicum"
    gdir = tmp_path / "reference" / "gallery" / slug
    gdir.mkdir(parents=True)
    (gdir / "manifest.json").write_text(
        json.dumps(
            [
                {"file": "1.jpg", "attribution": "ok", "passed_qa": True},
                {
                    "file": "2.jpg",
                    "attribution": "bad",
                    "passed_qa": False,
                    "qa_reasons": ["fruit-only"],
                },
                {"file": "3.jpg", "attribution": "legacy"},  # no passed_qa -> default-shown
            ]
        )
    )
    with SessionLocal() as db:
        t = _task_with_input(
            db, "Solanum lycopersicum — single-image → 3D reconstruction", "reference/x_ref.jpg"
        )
        urls = [r["url"] for r in service.reference_images_for_task(db, t)]
        assert any("1.jpg" in u for u in urls)  # passed_qa True -> shown
        assert not any("2.jpg" in u for u in urls)  # passed_qa False -> hidden
        assert any("3.jpg" in u for u in urls)  # legacy (unscored) -> default shown
        db.rollback()
