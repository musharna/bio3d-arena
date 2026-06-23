import trimesh
from sqlalchemy import select

from app import sourcing
from app.database import SessionLocal, init_db
from app.models import Category, ModelOutput, Task
from scripts.source_volumetric import ingest_volumetric

BARLEY = "Hordeum vulgare — barley root system (3D MRI)"


def setup_module(_m):
    init_db()


def _get_or_create_task(db, title):
    if db.query(Task).filter_by(title=title).first():
        return
    cat = db.query(Category).filter_by(slug="plants").first() or Category(
        slug="plants", name="Plants"
    )
    db.add(cat)
    db.flush()
    db.add(Task(category_id=cat.id, title=title, prompt="p"))
    db.commit()


def _fake_glb(path):
    return trimesh.creation.box().export(file_type="glb")


def test_ingest_volumetric_hosts_with_provenance():
    db = SessionLocal()
    try:
        _get_or_create_task(db, BARLEY)
        report = ingest_volumetric(
            db,
            ["/x/barley_root_01.nii"],
            dataset="ipk-barley-mri",
            to_glb=_fake_glb,
            task_title=BARLEY,
            modality="MRI",
        )
        assert report["hosted"] == 1
        task = db.execute(select(Task).where(Task.title == BARLEY)).scalars().first()
        out = (
            db.execute(
                select(ModelOutput).where(
                    ModelOutput.source == "mri:ipk-barley-mri",
                    ModelOutput.task_id == task.id,
                )
            )
            .scalars()
            .all()
        )
        assert out, "no volumetric output hosted on the barley subject"
        o = out[0]
        assert sourcing.source_class(o.source) == "volumetric"
        assert o.license == "CC-BY 4.0"
        assert "barley" in (o.attribution or "").lower()
    finally:
        db.close()


def test_ingest_volumetric_skips_unconvertible():
    from app.volume_convert import VolumeConvertError

    db = SessionLocal()
    try:
        _get_or_create_task(db, BARLEY)

        def raising(path):
            raise VolumeConvertError("no surface")

        report = ingest_volumetric(
            db,
            ["/x/bad.nii"],
            dataset="ipk-barley-mri",
            to_glb=raising,
            task_title=BARLEY,
            modality="MRI",
        )
        assert report["skipped"] == 1 and report["hosted"] == 0
    finally:
        db.close()
