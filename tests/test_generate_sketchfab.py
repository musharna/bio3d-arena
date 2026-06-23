import trimesh
from sqlalchemy import select

from app import sourcing
from app.database import SessionLocal, init_db
from app.mesh_convert import MeshConvertError
from app.models import Category, ModelOutput, Task
from scripts.generate_sketchfab import MAIZE_ASSETS, MAIZE_TITLE, ingest_sketchfab

TOMATO = "Solanum lycopersicum — single-image → 3D reconstruction"


def setup_module(_m):
    init_db()


def _tomato_task(db):
    cat = db.query(Category).filter_by(slug="plants").first() or Category(
        slug="plants", name="Plants"
    )
    db.add(cat)
    db.flush()
    db.add(Task(category_id=cat.id, title=TOMATO, prompt="p"))
    db.commit()


def _fake_glb(path):
    return trimesh.creation.box().export(file_type="glb")


def _asset(variant):
    return {
        "variant": variant,
        "uid": "abc123",
        "name": "Test Tomato",
        "author": variant,  # unique per test -> appears in attribution for read-back
        "license": "CC-BY 4.0",
        "keep": None,
    }


def test_ingest_sketchfab_hosts_found_with_cc_provenance(tmp_path):
    db = SessionLocal()
    try:
        _tomato_task(db)
        items = [(str(tmp_path / "a.glb"), _asset("sketchfab-tHost"))]
        report = ingest_sketchfab(db, items, to_glb=_fake_glb)
        assert report["hosted"] == 1
        out = (
            db.execute(
                select(ModelOutput).where(ModelOutput.attribution.contains("sketchfab-tHost"))
            )
            .scalars()
            .one()
        )
        assert out.source == "found:sketchfab"
        assert sourcing.source_class(out.source) == "found"  # game asset = found, not procedural
        assert (
            "CC-BY" in out.license and "sketchfab-tHost" in out.license
        )  # author label carried into license
        assert out.external_url and "sketchfab.com" in out.external_url
    finally:
        db.close()


def test_ingest_sketchfab_skips_unconvertible(tmp_path):
    db = SessionLocal()
    try:
        _tomato_task(db)

        def raising(path):
            raise MeshConvertError("bad glb")

        report = ingest_sketchfab(db, [("x.glb", _asset("sketchfab-tSkip"))], to_glb=raising)
        assert report["skipped"] == 1
        assert report["hosted"] == 0
    finally:
        db.close()


def test_ingest_sketchfab_scoring_failure_keeps_hosted_object(tmp_path):
    db = SessionLocal()
    try:
        _tomato_task(db)

        def boom_score(db_, out):
            raise RuntimeError("scorer unreachable")

        report = ingest_sketchfab(
            db,
            [(str(tmp_path / "i.glb"), _asset("sketchfab-tIso"))],
            to_glb=_fake_glb,
            score_fn=boom_score,
        )
        assert report["hosted"] == 1
        assert report["errors"] == 0
        out = (
            db.execute(
                select(ModelOutput).where(ModelOutput.attribution.contains("sketchfab-tIso"))
            )
            .scalars()
            .one()
        )
        assert out.asset_path
    finally:
        db.close()


def test_maize_assets_are_distinct_public_safe_cc():
    """Curated maize set: unique uids/variants, all hostable CC, all public-safe (no NC/ND)."""
    assert len(MAIZE_ASSETS) >= 3
    uids = [a["uid"] for a in MAIZE_ASSETS]
    variants = [a["variant"] for a in MAIZE_ASSETS]
    assert len(set(uids)) == len(uids), "duplicate Sketchfab uid in maize set"
    assert len(set(variants)) == len(variants), "duplicate variant slug in maize set"
    for a in MAIZE_ASSETS:
        assert sourcing.classify_license(a["license"]) == "host"
        assert sourcing.public_safe(a["license"]), f"{a['variant']} not public-safe: {a['license']}"


def test_ingest_sketchfab_routes_maize_to_maize_task():
    """Maize assets must attach to the Zea mays subject task."""
    db = SessionLocal()
    try:
        _task = db.query(Task).filter_by(title=MAIZE_TITLE).first()
        if _task is None:
            cat = db.query(Category).filter_by(slug="plants").first() or Category(
                slug="plants", name="Plants"
            )
            db.add(cat)
            db.flush()
            db.add(Task(category_id=cat.id, title=MAIZE_TITLE, prompt="p"))
            db.commit()
        item = (None, MAIZE_ASSETS[0])
        report = ingest_sketchfab(db, [item], to_glb=_fake_glb, task_title=MAIZE_TITLE)
        assert report["hosted"] == 1
        maize_task = db.execute(select(Task).where(Task.title == MAIZE_TITLE)).scalars().first()
        out = (
            db.execute(
                select(ModelOutput).where(
                    ModelOutput.source == "found:sketchfab",
                    ModelOutput.task_id == maize_task.id,
                )
            )
            .scalars()
            .all()
        )
        assert any(MAIZE_ASSETS[0]["author"] in (o.attribution or "") for o in out)
    finally:
        db.close()
