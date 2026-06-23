"""Rose (Track A3) spotlight subject + registry coverage (Tier 1)."""

from app.database import SessionLocal, init_db
from app.models import Task
from app.seed import ROSE_SUBJECT_TITLE, seed_rose_subject
from app.sourcing import SCAN_DATASETS, VOLUMETRIC_DATASETS, source_class
from app.spotlight import build_spotlight, find_spotlight


def setup_module(_m):
    init_db()


def test_rose_spotlight_registered():
    spot = find_spotlight("rose")
    assert spot is not None
    assert spot["task_title"] == ROSE_SUBJECT_TITLE
    assert spot["reference_image"] == "reference/rose_ref.jpg"


def test_rose_x_registries_cc0():
    # ROSE-X feeds BOTH the scan class (point clouds) and the volumetric class (X-ray CT).
    assert SCAN_DATASETS["rose-x"]["license"] == "CC0 1.0"
    assert VOLUMETRIC_DATASETS["rose-x"]["license"] == "CC0 1.0"
    assert VOLUMETRIC_DATASETS["rose-x"]["modality"] == "CT"
    assert source_class("ct:rose-x") == "volumetric"
    assert source_class("rose-x") == "scan"  # rose-x is a SCAN_DATASETS key


def test_seed_rose_subject_idempotent_and_buildable():
    db = SessionLocal()
    try:
        seed_rose_subject(db)
        seed_rose_subject(db)  # idempotent → no duplicate
        tasks = db.query(Task).filter_by(title=ROSE_SUBJECT_TITLE).all()
        assert len(tasks) == 1
        page = build_spotlight(db, "rose")
        assert page is not None
        assert page["title"] == ROSE_SUBJECT_TITLE
    finally:
        db.close()
