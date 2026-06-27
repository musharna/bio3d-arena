# tests/test_nvs.py
from __future__ import annotations

import io

import pytest
from PIL import Image

from app.image3d import Image3DError, _normalize_views, generate_nvs


def _sheet(cols=3, rows=2, tile=320, color=(0, 128, 0)):
    im = Image.new("RGB", (cols * tile, rows * tile), color)
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


def _png(color=(1, 2, 3)):
    b = io.BytesIO()
    Image.new("RGB", (320, 320), color).save(b, "PNG")
    return b.getvalue()


def test_normalize_detiles_single_sheet_into_six():
    views = _normalize_views([_sheet()], n_views=6, grid=(3, 2))
    assert len(views) == 6
    for v in views:
        assert Image.open(io.BytesIO(v)).size == (320, 320)


def test_normalize_passes_through_list_of_six():
    six = [_png((i, i, i)) for i in range(6)]
    assert _normalize_views(six, n_views=6, grid=(3, 2)) == six


def test_normalize_bad_count_raises():
    with pytest.raises(Image3DError):
        _normalize_views([_png(), _png(), _png()], n_views=6, grid=(3, 2))  # 3 ≠ 6 and ≠ 1


class _FakeNvsTransport:
    """submit→poll returns a SUCCEEDED status with a single tiled sheet (zero123++ shape)."""

    def submit(self, image_bytes, model, api_key):
        assert api_key == "k" and image_bytes
        return {"id": "x"}

    def poll(self, req, api_key):
        return "succeeded", [_sheet()]


def test_generate_nvs_returns_six_views():
    views = generate_nvs(b"img", api_key="k", model="m", transport=_FakeNvsTransport())
    assert len(views) == 6
