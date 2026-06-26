# tests/test_crops_api_recon.py
import pytest
from tests._coverage_helpers import assert_crop_entry
from scripts.generate_api_recon import CROPS


@pytest.mark.parametrize(
    "crop,title",
    [
        ("arabidopsis", "Arabidopsis thaliana — single-image → 3D reconstruction"),
        ("pinus", "Pinus sylvestris — single-image → 3D reconstruction"),
    ],
)
def test_new_recon_crops_wired(crop, title):
    assert crop in CROPS, crop
    assert CROPS[crop]["task_title"] == title
    assert_crop_entry(CROPS[crop], file_key="image")
