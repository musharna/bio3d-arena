from __future__ import annotations

import io

import numpy as np
from PIL import Image

from app import morphology
from app.input_grade import GradeResult, grade_input


def _img_bytes(w, h, *, busy=False):
    if busy:
        arr = (np.random.default_rng(0).integers(0, 256, size=(h, w, 3))).astype("uint8")
        im = Image.fromarray(arr, "RGB")
    else:
        im = Image.new("RGB", (w, h), (255, 255, 255))
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


ENTRY = morphology.STRATEGY[morphology.ROSETTE]


def test_heuristics_pass_large_plain():
    r = grade_input(
        _img_bytes(1200, 1200),
        growth_form=morphology.ROSETTE,
        strategy_entry=ENTRY,
        heuristics_only=True,
    )
    assert isinstance(r, GradeResult)
    assert r.dims_ok and r.bg_ok and r.vlm is None and r.verdict == "good"


def test_small_image_is_reject():
    r = grade_input(
        _img_bytes(512, 512),
        growth_form=morphology.ROSETTE,
        strategy_entry=ENTRY,
        heuristics_only=True,
    )
    assert not r.dims_ok and r.verdict == "reject"
    assert any("resolution" in s for s in r.reasons)


def test_busy_background_flags_bg():
    r = grade_input(
        _img_bytes(1200, 1200, busy=True),
        growth_form=morphology.ROSETTE,
        strategy_entry=ENTRY,
        heuristics_only=True,
    )
    assert not r.bg_ok and r.verdict == "marginal"
