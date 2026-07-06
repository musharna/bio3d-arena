import json
import os
import pytest

REQUIRED = {
    "subject",
    "file",
    "source",
    "source_url",
    "download_url",
    "license",
    "author",
    "attribution",
    "title",
    "note",
}
ALLOWED_LICENSES = {"CC0-1.0", "CC-BY-4.0", "CC-BY-SA-4.0", "CC-BY-3.0", "CC-BY-SA-3.0"}
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.mark.parametrize("slug", ["arabidopsis", "maize", "rose", "soybean", "tomato", "pinus"])
def test_reference_has_image_and_valid_provenance(slug):
    img = os.path.join(REPO_ROOT, f"data/assets/reference/{slug}_ref.jpg")
    meta = os.path.join(REPO_ROOT, f"data/assets/reference/{slug}_ref.json")
    if not (os.path.exists(img) and os.path.exists(meta)):
        # data/ is gitignored runtime state — absent on a checkout without the runtime volume.
        pytest.skip(f"runtime reference asset absent (gitignored): {slug}")
    assert os.path.getsize(img) > 5000, img
    with open(meta) as f:
        d = json.load(f)
    assert REQUIRED <= set(d), REQUIRED - set(d)
    assert d["file"] == f"{slug}_ref.jpg"
    assert d["license"] in ALLOWED_LICENSES, d["license"]
    assert d["source_url"].startswith("http") and d["download_url"].startswith("http")
    for k in ("author", "attribution", "title", "subject"):
        assert d.get(k, "").strip(), k


def test_bio3darena_recon_gated_on_redistribute(monkeypatch):
    import json
    from app import reference_provenance as rp
    from app.database import SessionLocal
    from app.models import Category, Generator, ModelOutput, Task

    monkeypatch.setattr(rp, "cleared_reference_taxa", lambda: {"tomato"})  # rose NOT cleared

    with SessionLocal() as db:
        cat = Category(slug="plants2", name="P")
        g = Generator(slug="internal-recon", name="internal", kind="model", paradigm="image_recon")
        db.add_all([cat, g])
        db.flush()
        t = Task(category_id=cat.id, title="rp-rose", prompt="p", active=True)
        db.add(t)
        db.flush()
        # bio3d-arena recon from an UN-cleared photo → must raise
        bad = ModelOutput(
            task_id=t.id,
            generator_id=g.id,
            asset_path="a.glb",
            source="bio3d-arena",
            meta_json=json.dumps({"input_image": "reference/rose_ref.jpg"}),
        )
        # bio3d-arena GT mesh (no input_image) → exempt
        gt = ModelOutput(
            task_id=t.id,
            generator_id=g.id,
            asset_path="gt.glb",
            source="bio3d-arena",
            meta_json="{}",
        )
        db.add_all([bad, gt])
        db.flush()

        import pytest

        with pytest.raises(rp.ReferenceProvenanceError):
            rp.assert_recon_photos_cleared(db, {bad.id})
        rp.assert_recon_photos_cleared(db, {gt.id})  # no raise — no input_image
        db.rollback()
