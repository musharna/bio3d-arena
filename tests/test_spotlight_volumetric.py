from app.database import SessionLocal, init_db
from app.models import Task
from app.seed import seed_volumetric_subjects
from app.spotlight import build_spotlight, find_spotlight

BARLEY = "Hordeum vulgare — barley root system (3D MRI)"


def setup_module(_m):
    init_db()


def test_barley_spotlight_registered():
    spot = find_spotlight("barley-mri")
    assert spot is not None
    assert spot["task_title"] == BARLEY


def test_seed_volumetric_subjects_idempotent_and_buildable():
    db = SessionLocal()
    try:
        seed_volumetric_subjects(db)
        seed_volumetric_subjects(db)  # idempotent → no duplicate
        tasks = db.query(Task).filter_by(title=BARLEY).all()
        assert len(tasks) == 1
        page = build_spotlight(db, "barley-mri")
        assert page is not None
        assert page["title"] == BARLEY
    finally:
        db.close()
