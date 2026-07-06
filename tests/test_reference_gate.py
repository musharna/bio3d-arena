import json
import uuid
import pytest
from app.reference_provenance import ReferenceProvenanceError, assert_recon_photos_cleared
from app.database import SessionLocal, init_db
from app.models import Generator, ModelOutput, Task


def setup_module(_m):
    init_db()


def _recon(db, input_image):
    g = Generator(slug=f"g-{uuid.uuid4().hex}", name="g", kind="model", paradigm="p")
    db.add(g)
    db.flush()
    t = Task(title=f"t-{uuid.uuid4().hex[:8]}", prompt="p", category_id=1)
    db.add(t)
    db.flush()
    o = ModelOutput(
        task_id=t.id,
        generator_id=g.id,
        asset_path="x.glb",
        asset_format="glb",
        source="api:fal:trellis",
        meta_json=json.dumps({"input_image": input_image}),
    )
    db.add(o)
    db.flush()
    return o


def test_raises_when_reference_sidecar_missing():
    with SessionLocal() as db:
        o = _recon(db, "reference/zzz_ref.jpg")  # 'zzz' has no sidecar
        with pytest.raises(ReferenceProvenanceError):
            assert_recon_photos_cleared(db, {o.id})
        db.rollback()


def test_raises_when_input_image_missing():
    with SessionLocal() as db:
        o = _recon(db, None)  # no input_image at all -> unidentifiable taxon
        with pytest.raises(ReferenceProvenanceError):
            assert_recon_photos_cleared(db, {o.id})
        db.rollback()


def test_raises_when_input_image_unparseable():
    with SessionLocal() as db:
        o = _recon(db, "reference/nomatch.jpg")  # no '<taxon>_ref' match
        with pytest.raises(ReferenceProvenanceError):
            assert_recon_photos_cleared(db, {o.id})
        db.rollback()
