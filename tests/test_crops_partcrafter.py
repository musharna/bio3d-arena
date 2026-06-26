import pytest
from tests._coverage_helpers import assert_crop_entry
from scripts.generate_partcrafter import CROPS


@pytest.mark.parametrize(
    "crop,title,tag",
    [
        ("arabidopsis", "Arabidopsis thaliana — single-image → 3D reconstruction", "arabidopsis"),
        ("pinus", "Pinus sylvestris — single-image → 3D reconstruction", "pinus"),
    ],
)
def test_new_partcrafter_crops_wired(crop, title, tag):
    assert crop in CROPS
    assert CROPS[crop]["task_title"] == title
    assert CROPS[crop]["tag"] == tag
    assert_crop_entry(CROPS[crop], file_key="image")
