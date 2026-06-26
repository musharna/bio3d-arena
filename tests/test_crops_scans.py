# tests/test_crops_scans.py
from app.sourcing import SCAN_DATASETS
from scripts.source_scans import SCAN_TASKS


def test_arabidopsis_scan_task_registered():
    assert "arabidopsis" in SCAN_TASKS
    assert SCAN_TASKS["arabidopsis"] == "Arabidopsis thaliana — single-image → 3D reconstruction"


def test_romi_arabidopsis_dataset_metadata_truthful():
    meta = SCAN_DATASETS["romi-arabidopsis"]
    assert meta["license"] == "CC-BY-4.0"
    assert "ROMI" in meta["attribution"]
    assert "10379172" in meta["attribution"] or "10379172" in meta["url"]
    assert meta["url"].startswith("https://zenodo.org/records/10379172")
