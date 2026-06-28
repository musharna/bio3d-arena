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


class _FakeBlock:
    def __init__(self, payload):
        self.type = "tool_use"
        self.name = "record_input_grade"
        self.input = payload


class _FakeResp:
    def __init__(self, payload):
        self.content = [_FakeBlock(payload)]


class _FakeClient:
    def __init__(self, payload):
        self._payload = payload

    class _Messages:
        def __init__(self, payload):
            self._payload = payload

        def create(self, **_kw):
            return _FakeResp(self._payload)

    @property
    def messages(self):
        return _FakeClient._Messages(self._payload)


_GOOD_VLM = {
    "growth_form": morphology.ROSETTE,
    "background_ok": True,
    "view_matches_recipe": True,
    "fill_ok": True,
    "verdict": "good",
    "reasons": "clean top-down rosette on white",
}


def test_grade_with_vlm_parses_tool_block():
    from app.input_grade import grade_with_vlm

    out = grade_with_vlm(
        _FakeClient(_GOOD_VLM),
        _img_bytes(1200, 1200),
        growth_form=morphology.ROSETTE,
        strategy_entry=ENTRY,
    )
    assert out["growth_form"] == morphology.ROSETTE and out["verdict"] == "good"


def test_grade_input_flags_growth_form_mismatch():
    payload = dict(_GOOD_VLM, growth_form=morphology.SHRUB)  # disagrees with seed
    r = grade_input(
        _img_bytes(1200, 1200),
        growth_form=morphology.ROSETTE,
        strategy_entry=ENTRY,
        client=_FakeClient(payload),
    )
    assert r.vlm is not None and r.growth_form_match is False
    assert any("seed says" in s for s in r.reasons)


class _ErrorClient:
    """Fake client whose messages.create always raises — exercises the key-safety except branch."""

    class _Messages:
        def create(self, **_kw):
            raise RuntimeError("boom")

    @property
    def messages(self):
        return _ErrorClient._Messages()


def test_vlm_error_degrades_to_heuristics():
    """VLM error must be recorded as type-name only and verdict falls back to heuristics."""
    r = grade_input(
        _img_bytes(1200, 1200),
        growth_form=morphology.ROSETTE,
        strategy_entry=ENTRY,
        client=_ErrorClient(),
    )
    assert r.vlm is None
    assert r.growth_form_match is None
    assert any("vlm_error: RuntimeError" in s for s in r.reasons)
    assert r.verdict == "good"  # 1200x1200 plain white passes heuristics


def _jpeg_bytes(w=1200, h=1200):
    b = io.BytesIO()
    Image.new("RGB", (w, h), (255, 255, 255)).save(b, "JPEG")
    return b.getvalue()


def test_media_type_sniffed_from_bytes():
    from app.input_grade import _media_type

    assert _media_type(_jpeg_bytes(8, 8)) == "image/jpeg"
    assert _media_type(_img_bytes(8, 8)) == "image/png"  # _img_bytes saves PNG


class _CapturingClient:
    """Records the messages sent so the image block's media_type can be asserted."""

    def __init__(self, payload):
        self._payload = payload
        self.sent = {}

    class _Messages:
        def __init__(self, outer):
            self._outer = outer

        def create(self, **kw):
            self._outer.sent = kw
            return _FakeResp(self._outer._payload)

    @property
    def messages(self):
        return _CapturingClient._Messages(self)


def test_grade_with_vlm_declares_media_type_matching_input():
    """Regression: a JPEG reference photo must be sent as image/jpeg, not hardcoded image/png
    (the mismatch caused a live BadRequestError)."""
    from app.input_grade import grade_with_vlm

    client = _CapturingClient(_GOOD_VLM)
    grade_with_vlm(client, _jpeg_bytes(), growth_form=morphology.ROSETTE, strategy_entry=ENTRY)
    blocks = client.sent["messages"][0]["content"]
    img = next(b for b in blocks if b.get("type") == "image")
    assert img["source"]["media_type"] == "image/jpeg"
