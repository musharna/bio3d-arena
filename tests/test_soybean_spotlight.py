"""Soybean (Track A2 legume) spotlight subject + registry coverage."""

from app.database import SessionLocal, init_db
from app.models import Task
from app.seed import SOYBEAN_SUBJECT_TITLE, seed_soybean_subject
from app.sourcing import SCAN_DATASETS, source_class
from app.spotlight import build_spotlight, find_spotlight


def setup_module(_m):
    init_db()


def test_soybean_spotlight_registered():
    spot = find_spotlight("soybean")
    assert spot is not None
    assert spot["task_title"] == SOYBEAN_SUBJECT_TITLE
    assert spot["reference_image"] == "reference/soybean_ref.jpg"


def test_icrisat_legume_scan_registry():
    d = SCAN_DATASETS["icrisat-legume"]
    assert d["license"] == "CC-BY 4.0"
    assert "10.6084/m9.figshare.28270742" in d["url"]
    assert source_class("icrisat-legume") == "scan"


def test_soybean_in_scan_tasks():
    from scripts.source_scans import SCAN_TASKS

    assert SCAN_TASKS["soybean"] == SOYBEAN_SUBJECT_TITLE


def test_soybean_in_crop_parametric_scripts():
    # Tier 2/3: soybean wired into found (sketchfab), frontier (partcrafter), recon (api), procedural (demeter).
    from scripts.generate_api_recon import CROPS as RECON_CROPS
    from scripts.generate_demeter import SPECIES_TASKS
    from scripts.generate_partcrafter import CROPS as PC_CROPS
    from scripts.generate_sketchfab import CROPS as SF_CROPS

    assert SF_CROPS["soybean"]["task_title"] == SOYBEAN_SUBJECT_TITLE
    assert len(SF_CROPS["soybean"]["assets"]) >= 3
    assert PC_CROPS["soybean"]["image"].endswith("soybean_ref.jpg")
    assert RECON_CROPS["soybean"]["task_title"] == SOYBEAN_SUBJECT_TITLE
    assert SPECIES_TASKS["soybean"] == SOYBEAN_SUBJECT_TITLE


def test_seed_soybean_subject_idempotent_and_buildable():
    db = SessionLocal()
    try:
        seed_soybean_subject(db)
        seed_soybean_subject(db)  # idempotent → no duplicate
        tasks = db.query(Task).filter_by(title=SOYBEAN_SUBJECT_TITLE).all()
        assert len(tasks) == 1
        page = build_spotlight(db, "soybean")
        assert page is not None and page["title"] == SOYBEAN_SUBJECT_TITLE
    finally:
        db.close()
