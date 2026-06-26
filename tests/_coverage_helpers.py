# tests/_coverage_helpers.py
import os

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Verbatim registry of seeded task titles. Extend when a new crop/subject title is seeded.
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
        path = os.path.join(REPO_ROOT, entry[file_key])
        if not os.path.exists(path):
            # data/ is gitignored runtime state; on a checkout without the runtime volume
            # the input asset is legitimately absent — skip rather than hard-fail.
            pytest.skip(f"runtime asset absent (gitignored): {entry[file_key]}")
        assert os.path.getsize(path) > 0, entry[file_key]
