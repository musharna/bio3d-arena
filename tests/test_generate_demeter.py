import json

import numpy as np
import trimesh
from sqlalchemy import select

from app import sourcing
from app.database import SessionLocal, init_db
from app.mesh_convert import MeshConvertError
from app.models import Category, ModelOutput, Task
from scripts.generate_demeter import ingest_demeter, obj_to_glb_yup

MAIZE = "Zea mays — single-image → 3D reconstruction"


def setup_module(_m):
    init_db()


def _maize_task(db):
    cat = db.query(Category).filter_by(slug="plants").first() or Category(
        slug="plants", name="Plants"
    )
    db.add(cat)
    db.flush()
    db.add(Task(category_id=cat.id, title=MAIZE, prompt="p"))
    db.commit()


def _fake_to_glb(path):
    return trimesh.load(path, force="mesh").export(file_type="glb")


def test_ingest_demeter_hosts_procedural(tmp_path):
    db = SessionLocal()
    try:
        _maize_task(db)
        obj = tmp_path / "demeter_maize_dHost.obj"
        trimesh.creation.box().export(str(obj))
        report = ingest_demeter(db, [str(obj)], to_glb=_fake_to_glb, variant="dHost")
        assert report["hosted"] == 1
        out = (
            db.execute(select(ModelOutput).where(ModelOutput.attribution.contains("dHost")))
            .scalars()
            .one()
        )
        assert out.source == "procedural:demeter"
        assert sourcing.source_class(out.source) == "procedural"
        assert "Demeter" in out.license and "re-vet" in out.license  # academic-NC + re-vet flag
        assert json.loads(out.meta_json)["variant"] == "dHost"
    finally:
        db.close()


def test_ingest_demeter_caveat_flows_into_meta(tmp_path):
    db = SessionLocal()
    try:
        _maize_task(db)
        obj = tmp_path / "demeter_maize_dCav.obj"
        trimesh.creation.box().export(str(obj))
        ingest_demeter(
            db,
            [str(obj)],
            to_glb=_fake_to_glb,
            variant="dCav",
            caveat="learned PCA-morphable — clumpy",
        )
        out = (
            db.execute(select(ModelOutput).where(ModelOutput.attribution.contains("dCav")))
            .scalars()
            .one()
        )
        assert json.loads(out.meta_json)["caveat"] == "learned PCA-morphable — clumpy"
    finally:
        db.close()


def test_ingest_demeter_skips_unconvertible(tmp_path):
    db = SessionLocal()
    try:
        _maize_task(db)

        def raising_to_glb(path):
            raise MeshConvertError("bad mesh")

        report = ingest_demeter(db, [str(tmp_path / "x.obj")], to_glb=raising_to_glb)
        assert report["skipped"] == 1
        assert report["hosted"] == 0
    finally:
        db.close()


def test_obj_to_glb_yup_rotates_z_up_to_y_up(tmp_path):
    """A Z-tall Demeter mesh must come out Y-tall (model-viewer is +Y up)."""
    # a box tall in Z (extent Z > X,Y) — mimics Demeter's stem-along-Z alignment
    box = trimesh.creation.box(extents=(0.3, 0.3, 1.0))
    obj = tmp_path / "ztall.obj"
    box.export(str(obj))
    glb = obj_to_glb_yup(str(obj))
    assert isinstance(glb, (bytes, bytearray)) and len(glb) > 0
    rt = trimesh.load(trimesh.util.wrap_as_stream(glb), file_type="glb", force="mesh")
    ex = rt.extents
    # after rotation the long axis is Y, not Z
    assert ex[1] == max(ex)
    assert ex[1] > ex[2]
    # recentred near the origin
    assert np.allclose(rt.bounds.mean(axis=0), 0, atol=1e-6)


def test_obj_to_glb_yup_yaws_broad_side_to_camera(tmp_path):
    """A distichous plant broad along depth should be yawed so the broad face is camera-facing
    (broad horizontal extent ends up along X, the default model-viewer front axis)."""
    # Z-up input, broad along Y (0.4) vs narrow X (0.1); after Z→Y the broad spread lands on depth
    box = trimesh.creation.box(extents=(0.1, 0.4, 1.0))
    obj = tmp_path / "distichous.obj"
    box.export(str(obj))
    rt = trimesh.load(
        trimesh.util.wrap_as_stream(obj_to_glb_yup(str(obj))), file_type="glb", force="mesh"
    )
    ex = rt.extents
    assert ex[1] == max(ex)  # still upright (Y tallest)
    assert ex[0] > ex[2]  # broad horizontal spread faces the camera (X), narrow in depth (Z)


def test_obj_to_glb_yup_rejects_empty(tmp_path):
    empty = tmp_path / "empty.obj"
    empty.write_text("# no geometry\n")
    try:
        obj_to_glb_yup(str(empty))
        raise AssertionError("expected MeshConvertError")
    except MeshConvertError:
        pass
