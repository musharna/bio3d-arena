# tests/test_crops_lpy.py
import os

import pytest

from scripts.generate_lpy import CROPS
from tests._coverage_helpers import assert_crop_entry


@pytest.mark.parametrize(
    "crop,model,title",
    [
        (
            "arabidopsis",
            "lpy/arabidopsis.lpy",
            "Arabidopsis thaliana — single-image → 3D reconstruction",
        ),
        ("pinus", "lpy/pine.lpy", "Pinus sylvestris — single-image → 3D reconstruction"),
    ],
)
def test_new_lpy_crops_wired(crop, model, title):
    assert crop in CROPS
    assert CROPS[crop]["model"] == model
    assert CROPS[crop]["variant"] == crop
    assert CROPS[crop]["task_title"] == title
    assert_crop_entry(CROPS[crop])  # title check; model-file existence checked by Step 4 run
    assert os.path.exists(model), model
