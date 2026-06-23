import json

import trimesh
from sqlalchemy import select

from app import sourcing
from app.database import SessionLocal, init_db
from app.mesh_convert import MeshConvertError
from app.models import Category, ModelOutput, Task
from scripts.generate_partcrafter import CROPS, MAIZE_TITLE, ingest_partcrafter

TOMATO = "Solanum lycopersicum — single-image → 3D reconstruction"


def setup_module(_m):
    init_db()


def _task(db, title):
    # Get-or-create: the test DB is shared across modules, so adding a duplicate-title task on
    # every call would make `Task.title == X` ambiguous for `.one()` lookups in later assertions.
    if db.query(Task).filter_by(title=title).first():
        return
    cat = db.query(Category).filter_by(slug="plants").first() or Category(
        slug="plants", name="Plants"
    )
    db.add(cat)
    db.flush()
    db.add(Task(category_id=cat.id, title=title, prompt="p"))
    db.commit()


def _tomato_task(db):
    _task(db, TOMATO)


def _fake_glb(path):
    return trimesh.creation.box().export(file_type="glb")


def test_frontier_source_class_is_ai():
    assert sourcing.source_class("frontier:partcrafter") == "ai"  # neural frontier = AI recon


def test_crops_config_covers_maize():
    maize = CROPS["maize"]
    assert maize["task_title"] == MAIZE_TITLE
    assert maize["tag"] == "maize"
    assert maize["image"].endswith("maize_ref.jpg")


def test_ingest_partcrafter_routes_to_maize_task(tmp_path):
    """A maize run must attach to the Zea mays subject task, not tomato's."""
    db = SessionLocal()
    try:
        _task(db, MAIZE_TITLE)
        report = ingest_partcrafter(
            db,
            [(str(tmp_path / "m.glb"), 3)],
            to_glb=_fake_glb,
            variant="maize",
            task_title=MAIZE_TITLE,
        )
        assert report["hosted"] == 1
        maize_task = db.execute(select(Task).where(Task.title == MAIZE_TITLE)).scalars().first()
        rows = (
            db.execute(
                select(ModelOutput).where(
                    ModelOutput.source == "frontier:partcrafter",
                    ModelOutput.task_id == maize_task.id,
                )
            )
            .scalars()
            .all()
        )
        assert any("maize" in (o.meta_json or "") for o in rows)
    finally:
        db.close()


def test_ingest_partcrafter_hosts_frontier(tmp_path):
    db = SessionLocal()
    try:
        _tomato_task(db)
        report = ingest_partcrafter(
            db, [(str(tmp_path / "o.glb"), 3)], to_glb=_fake_glb, variant="pcHost"
        )
        assert report["hosted"] == 1
        out = (
            db.execute(select(ModelOutput).where(ModelOutput.source == "frontier:partcrafter"))
            .scalars()
            .all()
        )
        hit = [o for o in out if "pcHost" in o.meta_json]
        assert len(hit) == 1
        assert sourcing.source_class(hit[0].source) == "ai"
        assert "MIT" in hit[0].license
        meta = json.loads(hit[0].meta_json)
        assert (
            meta["modality"] == "part-based"
            and meta["n_parts"] == 3
            and meta["self_hosted"] is True
        )
    finally:
        db.close()


def test_ingest_partcrafter_skips_unconvertible(tmp_path):
    db = SessionLocal()
    try:
        _tomato_task(db)

        def raising(path):
            raise MeshConvertError("bad glb")

        report = ingest_partcrafter(
            db, [(str(tmp_path / "x.glb"), 3)], to_glb=raising, variant="pcSkip"
        )
        assert report["skipped"] == 1 and report["hosted"] == 0
    finally:
        db.close()


def test_ingest_partcrafter_scoring_failure_keeps_hosted(tmp_path):
    db = SessionLocal()
    try:
        _tomato_task(db)

        def boom(db_, out):
            raise RuntimeError("scorer unreachable")

        report = ingest_partcrafter(
            db, [(str(tmp_path / "i.glb"), 3)], to_glb=_fake_glb, score_fn=boom, variant="pcIso"
        )
        assert report["hosted"] == 1 and report["errors"] == 0
    finally:
        db.close()
