# tests/test_export_postures.py
import uuid
from app.public_export import IncludeSet, filter_include_for_posture, is_commercial_model
from app.database import SessionLocal, init_db
from app.models import Generator, ModelOutput, Task


def setup_module(_m):
    init_db()


def _o(db, source, license_):
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
        source=source,
        license=license_,
    )
    db.add(o)
    db.flush()
    return o


def test_commercial_model_predicate():
    assert is_commercial_model("api:fal:trellis")
    assert is_commercial_model("recon:trellis-mv")
    assert is_commercial_model("frontier:partcrafter")
    assert not is_commercial_model("plant3d")
    assert not is_commercial_model("bio3d-arena")


def test_redistribute_drops_commercial_and_keeps_cc():
    with SessionLocal() as db:
        cc = _o(db, "plant3d", "CC0-1.0")
        comm = _o(db, "api:fal:trellis", "TRELLIS (fal) generated-asset terms")
        xf = _o(db, "found:xfrog", "XfrogPlants commercial")
        inc = IncludeSet(output_ids={cc.id, comm.id, xf.id})
        filter_include_for_posture(db, inc, "redistribute", gated=set())
        assert inc.output_ids == {cc.id}
        db.rollback()


def test_display_keeps_commercial_drops_hardexclude_and_gated():
    with SessionLocal() as db:
        cc = _o(db, "plant3d", "CC0-1.0")
        comm = _o(db, "api:fal:trellis", "TRELLIS (fal) generated-asset terms")
        xf = _o(db, "found:xfrog", "XfrogPlants commercial")
        inc = IncludeSet(output_ids={cc.id, comm.id, xf.id})
        filter_include_for_posture(db, inc, "display", gated={cc.id})  # cc gated out
        assert inc.output_ids == {comm.id}  # commercial kept, xfrog + gated dropped
        db.rollback()
