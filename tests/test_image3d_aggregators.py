import pytest
import trimesh

from app.image3d import Image3DError, generate_fal, generate_replicate


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

    def submit(self, source, model, api_key, mode="image"):
        self.calls.append(("submit", model))
        self.last_source, self.last_mode = source, mode
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


class FakeReplicateTransport:
    def __init__(self, poll_statuses, glb_url, glb):
        self._statuses = list(poll_statuses)
        self._glb_url = glb_url
        self._glb = glb
        self.calls = []

    def submit(self, source, model, api_key, mode="image"):
        self.calls.append(("submit", model))
        self.last_source, self.last_mode = source, mode
        return {"get_url": "https://api.replicate.com/v1/predictions/p1"}

    def poll(self, req, api_key):
        self.calls.append("poll")
        s = self._statuses.pop(0) if len(self._statuses) > 1 else self._statuses[0]
        return s, (self._glb_url if s.lower() == "succeeded" else None)

    def download(self, url):
        self.calls.append("download")
        return self._glb


def test_generate_replicate_runs_and_returns_glb():
    glb = _box_glb()
    t = FakeReplicateTransport(["processing", "succeeded"], "https://rep/x.glb", glb)
    out = generate_replicate(
        b"img", api_key="k", model="firtoz/trellis", transport=t, poll_interval_s=0
    )
    assert out == glb
    assert t.calls == [("submit", "firtoz/trellis"), "poll", "poll", "download"]


def test_generate_replicate_raises_on_failed():
    t = FakeReplicateTransport(["failed"], "https://rep/x.glb", _box_glb())
    with pytest.raises(Image3DError):
        generate_replicate(b"img", api_key="k", model="m", transport=t, poll_interval_s=0)


def test_generate_replicate_times_out():
    t = FakeReplicateTransport(["processing"], "https://rep/x.glb", _box_glb())
    with pytest.raises(Image3DError):
        generate_replicate(
            b"img", api_key="k", model="m", transport=t, timeout_s=0, poll_interval_s=0
        )


def test_generate_fal_text_mode_passes_prompt():
    glb = _box_glb()
    t = FakeFalTransport(["COMPLETED"], "https://fal/x.glb", glb)
    out = generate_fal(
        "a tomato plant", api_key="k", model="fal-ai/hunyuan3d-v3/text-to-3d",
        mode="text", transport=t, poll_interval_s=0,
    )
    assert out == glb
    assert t.last_mode == "text" and t.last_source == "a tomato plant"


def test_generate_replicate_text_mode_passes_prompt():
    glb = _box_glb()
    t = FakeReplicateTransport(["succeeded"], "https://rep/x.glb", glb)
    out = generate_replicate(
        "a tomato fruit", api_key="k", model="tencent/hunyuan-3d-3.1",
        mode="text", transport=t, poll_interval_s=0,
    )
    assert out == glb
    assert t.last_mode == "text" and t.last_source == "a tomato fruit"


def test_text_providers_catalog():
    import functools

    from app.image3d import TEXT_PROVIDERS

    fal = {k: v for k, v in TEXT_PROVIDERS.items() if k.startswith("fal:")}
    rep = {k: v for k, v in TEXT_PROVIDERS.items() if k.startswith("replicate:")}
    assert len(fal) >= 3 and all(v[1] == "FAL_KEY" for v in fal.values())
    assert len(rep) >= 2 and all(v[1] == "REPLICATE_API_TOKEN" for v in rep.values())
    assert all(k.endswith("-text") for k in TEXT_PROVIDERS)
    # text partials pre-bind mode="text" so the adapter can call fn(prompt, api_key=...)
    fn = TEXT_PROVIDERS["fal:hunyuan3d-v3-text"][0]
    assert isinstance(fn, functools.partial) and fn.keywords.get("mode") == "text"


def test_providers_registry_catalog():
    from app.image3d import PROVIDERS

    # direct + both aggregators present, sharing the right env vars
    assert PROVIDERS["tripo"][1] == "TRIPO_API_KEY"
    fal = {k: v for k, v in PROVIDERS.items() if k.startswith("fal:")}
    rep = {k: v for k, v in PROVIDERS.items() if k.startswith("replicate:")}
    assert len(fal) >= 5 and all(v[1] == "FAL_KEY" for v in fal.values())
    assert len(rep) >= 4 and all(v[1] == "REPLICATE_API_TOKEN" for v in rep.values())
    # the model is pre-bound via functools.partial, so the adapter can call fn(image, api_key=...)
    import functools

    fn = PROVIDERS["fal:trellis"][0]
    assert isinstance(fn, functools.partial)
    assert fn.keywords.get("model") == "fal-ai/trellis"


def test_fal_transport_multiview_builds_image_urls():
    """The real FalTransport.submit multiview branch packs N views into image_urls (no network)."""
    from app.image3d import FalTransport

    captured = {}

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"request_id": "r", "status_url": "s", "response_url": "x"}

    class FakeClient:
        def post(self, url, headers, json):
            captured["json"] = json
            return FakeResp()

    t = FalTransport(client=FakeClient())
    t.submit([b"v1", b"v2"], "fal-ai/trellis/multi", "k", "multiview")
    urls = captured["json"]["input"]["image_urls"]
    assert len(urls) == 2 and all(u.startswith("data:image") for u in urls)
