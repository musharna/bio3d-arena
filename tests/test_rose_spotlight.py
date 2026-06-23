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


def test_rose_in_crop_parametric_scripts():
    # Tier 2 found + frontier: rose wired into the crop-parametric ingest scripts.
    from scripts.generate_partcrafter import CROPS as PC_CROPS
    from scripts.generate_sketchfab import CROPS as SF_CROPS
    from scripts.source_scans import SCAN_TASKS

    rose_title = "Rosa — single-image → 3D reconstruction"
    assert SCAN_TASKS["rose"] == rose_title
    assert SF_CROPS["rose"]["task_title"] == rose_title
    assert len(SF_CROPS["rose"]["assets"]) >= 3
    assert PC_CROPS["rose"]["task_title"] == rose_title
    assert PC_CROPS["rose"]["image"].endswith("rose_ref.jpg")


def test_rose_in_procedural_scripts():
    # Tier 3 procedural (flowerless caveats): rose wired into demeter + agrigen.
    from scripts.generate_agrigen import AGRIGEN_CROPS
    from scripts.generate_demeter import SPECIES_TASKS

    rose_title = "Rosa — single-image → 3D reconstruction"
    assert SPECIES_TASKS["rose"] == rose_title
    assert AGRIGEN_CROPS["rose"]["task_title"] == rose_title
    assert "bloom" in AGRIGEN_CROPS["rose"]["caveat"].lower()  # honest flowerless caveat


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
