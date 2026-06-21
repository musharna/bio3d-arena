import pytest
import trimesh

from app.image3d import Image3DError, generate_fal


def _box_glb() -> bytes:
    return trimesh.creation.box().export(file_type="glb")


class FakeFalTransport:
    """Drives generate_fal's submit→poll→download without network.
    poll_statuses consumed one per call (last repeats); resolves the GLB url on success."""

    def __init__(self, poll_statuses, glb_url, glb):
        self._statuses = list(poll_statuses)
        self._glb_url = glb_url
        self._glb = glb
        self.calls = []

    def submit(self, image_bytes, model, api_key):
        self.calls.append(("submit", model))
        return {"request_id": "r1"}

    def poll(self, req, api_key):
        self.calls.append("poll")
        s = self._statuses.pop(0) if len(self._statuses) > 1 else self._statuses[0]
        return s, (self._glb_url if s.lower() in ("completed", "succeeded") else None)

    def download(self, url):
        self.calls.append("download")
        assert url == self._glb_url
        return self._glb


def test_generate_fal_runs_and_returns_glb():
    glb = _box_glb()
    t = FakeFalTransport(["IN_PROGRESS", "COMPLETED"], "https://fal/x.glb", glb)
    out = generate_fal(b"img", api_key="k", model="fal-ai/trellis", transport=t, poll_interval_s=0)
    assert out == glb
    assert t.calls == [("submit", "fal-ai/trellis"), "poll", "poll", "download"]


def test_generate_fal_raises_on_failed():
    t = FakeFalTransport(["FAILED"], "https://fal/x.glb", _box_glb())
    with pytest.raises(Image3DError):
        generate_fal(b"img", api_key="k", model="m", transport=t, poll_interval_s=0)


def test_generate_fal_times_out():
    t = FakeFalTransport(["IN_PROGRESS"], "https://fal/x.glb", _box_glb())
    with pytest.raises(Image3DError):
        generate_fal(b"img", api_key="k", model="m", transport=t, timeout_s=0, poll_interval_s=0)
