# tests/_coverage_helpers.py
import os

KNOWN_TITLES = {
    "Solanum lycopersicum — single-image → 3D reconstruction",
    "Zea mays — single-image → 3D reconstruction",
    "Rosa — single-image → 3D reconstruction",
    "Glycine max — single-image → 3D reconstruction",
    "Arabidopsis thaliana — single-image → 3D reconstruction",
    "Pinus sylvestris — single-image → 3D reconstruction",
}


def assert_crop_entry(entry, *, file_key=None):
    """Common checks for a per-crop config entry: known task title, and (if file_key given)
    that the referenced input file exists relative to repo root (cwd in the test run)."""
    assert entry["task_title"] in KNOWN_TITLES, entry["task_title"]
    if file_key is not None:
        assert os.path.exists(entry[file_key]), entry[file_key]
