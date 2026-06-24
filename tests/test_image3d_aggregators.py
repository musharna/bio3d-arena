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

    def submit(
        self, source, model, api_key, mode="image", image_field="image_url", extra_input=None
    ):
        self.calls.append(("submit", model))
        self.last_source, self.last_mode, self.last_field = source, mode, image_field
        self.last_extra = extra_input
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
        "a tomato plant",
        api_key="k",
        model="fal-ai/hunyuan3d-v3/text-to-3d",
        mode="text",
        transport=t,
        poll_interval_s=0,
    )
    assert out == glb
    assert t.last_mode == "text" and t.last_source == "a tomato plant"


def test_generate_replicate_text_mode_passes_prompt():
    glb = _box_glb()
    t = FakeReplicateTransport(["succeeded"], "https://rep/x.glb", glb)
    out = generate_replicate(
        "a tomato fruit",
        api_key="k",
        model="tencent/hunyuan-3d-3.1",
        mode="text",
        transport=t,
        poll_interval_s=0,
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
    """The real FalTransport.submit multiview branch packs N views into image_urls (no network).
    The fal queue body carries input fields at the ROOT — no {"input": ...} wrapper."""
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
    assert "input" not in captured["json"]  # input fields live at the body root
    urls = captured["json"]["image_urls"]
    assert len(urls) == 2 and all(u.startswith("data:image") for u in urls)


def _tiny_jpeg() -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (32, 24), (200, 50, 50)).save(buf, "JPEG")
    return buf.getvalue()


def test_fal_transport_image_body_is_unwrapped_and_downscaled():
    """Image mode posts {"image_url": <data-uri>} at the body root (no wrapper)."""
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
    t.submit(_tiny_jpeg(), "fal-ai/trellis", "k", "image")
    assert "input" not in captured["json"]
    assert captured["json"]["image_url"].startswith("data:image/jpeg;base64,")


def test_replicate_transport_uses_version_endpoint_and_schema_field():
    """Submit GETs the model for its latest version + input schema, then POSTs to
    /predictions with {"version", "input"} using the schema's image field (images→list)."""
    from app.image3d import ReplicateTransport

    captured = {}

    class FakeResp:
        def __init__(self, payload):
            self._p = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self._p

    class FakeClient:
        def get(self, url, headers):
            captured["get_url"] = url
            return FakeResp(
                {
                    "latest_version": {
                        "id": "ver123",
                        "openapi_schema": {
                            "components": {
                                "schemas": {
                                    "Input": {
                                        "properties": {
                                            "images": {},
                                            "seed": {},
                                            "generate_model": {},
                                        }
                                    }
                                }
                            }
                        },
                    }
                }
            )

        def post(self, url, headers, json):
            captured["post_url"] = url
            captured["body"] = json
            return FakeResp({"urls": {"get": "https://api.replicate.com/v1/predictions/p1"}})

    t = ReplicateTransport(client=FakeClient())
    req = t.submit(_tiny_jpeg(), "firtoz/trellis", "k", "image")
    assert captured["get_url"].endswith("/models/firtoz/trellis")
    assert captured["post_url"].endswith(
        "/predictions"
    )  # version endpoint, not /models/.../predictions
    assert captured["body"]["version"] == "ver123"
    imgs = captured["body"]["input"]["images"]  # 'images' schema field → list
    assert isinstance(imgs, list) and imgs[0].startswith("data:image")
    assert req["get_url"].endswith("/predictions/p1")


def _capture_fal_submit(image_field):
    """Drive the real FalTransport.submit for an image and return the posted body."""
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

    FalTransport(client=FakeClient()).submit(_tiny_jpeg(), "m", "k", "image", image_field)
    return captured["json"]


def test_fal_image_field_single_vs_list():
    """Per-model image field: a *_url field is a string; a *_urls field is a single-item list."""
    body = _capture_fal_submit("input_image_url")  # hunyuan3d
    assert isinstance(body["input_image_url"], str)
    assert body["input_image_url"].startswith("data:image")

    body = _capture_fal_submit("input_image_urls")  # rodin/hyper3d
    assert isinstance(body["input_image_urls"], list)
    assert body["input_image_urls"][0].startswith("data:image")


def test_generate_fal_threads_image_field_to_submit():
    """generate_fal forwards the model's image_field down to the transport."""
    t = FakeFalTransport(["COMPLETED"], "https://fal/x.glb", _box_glb())
    generate_fal(
        b"img",
        api_key="k",
        model="fal-ai/hyper3d/rodin",
        image_field="input_image_urls",
        transport=t,
        poll_interval_s=0,
    )
    assert t.last_field == "input_image_urls"


def test_fal_extra_input_merged_into_body():
    """Model-specific params (e.g. hunyuan3d/v2 textured_mesh) merge into the body root."""
    body = None

    from app.image3d import FalTransport

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"request_id": "r", "status_url": "s", "response_url": "x"}

    captured = {}

    class FakeClient:
        def post(self, url, headers, json):
            captured["json"] = json
            return FakeResp()

    FalTransport(client=FakeClient()).submit(
        _tiny_jpeg(), "m", "k", "image", "input_image_url", {"textured_mesh": True}
    )
    body = captured["json"]
    assert body["input_image_url"].startswith("data:image")
    assert body["textured_mesh"] is True


def test_providers_hunyuan_v2_requests_textured_mesh():
    """hunyuan3d-v2 must request textured_mesh — its default is an untextured white mesh."""
    from app.image3d import PROVIDERS

    fn = PROVIDERS["fal:hunyuan3d-v2"][0]
    assert fn.keywords.get("extra_input") == {"textured_mesh": True}
    assert fn.keywords.get("image_field") == "input_image_url"


def test_send_with_retry_backs_off_then_succeeds():
    """A 429 then a 200 → retried and the 200 returned (no sleep in the test)."""
    import app.image3d as m

    calls = {"n": 0}

    class Resp:
        def __init__(self, code):
            self.status_code = code
            self.headers = {}

    def flaky():
        calls["n"] += 1
        return Resp(429) if calls["n"] == 1 else Resp(200)

    orig = m.time.sleep
    m.time.sleep = lambda *_: None
    try:
        r = m._send_with_retry(flaky, base_delay=0)
    finally:
        m.time.sleep = orig
    assert r.status_code == 200 and calls["n"] == 2


def test_send_with_retry_retries_transport_error():
    """A network/SSL TransportError is retried, not propagated, when attempts remain."""
    import httpx

    import app.image3d as m

    calls = {"n": 0}

    class Resp:
        status_code = 200
        headers: dict = {}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ReadError("ssl bad record mac")
        return Resp()

    orig = m.time.sleep
    m.time.sleep = lambda *_: None
    try:
        r = m._send_with_retry(flaky, base_delay=0)
    finally:
        m.time.sleep = orig
    assert r.status_code == 200 and calls["n"] == 2


def test_send_with_retry_heals_flaky_403():
    """fal's transient post-top-up 403 lock-flap is retried, then succeeds."""
    import app.image3d as m

    calls = {"n": 0}

    class Resp:
        def __init__(self, c):
            self.status_code = c
            self.headers = {}

    def flaky():
        calls["n"] += 1
        return Resp(403) if calls["n"] == 1 else Resp(200)

    orig = m.time.sleep
    m.time.sleep = lambda *_: None
    try:
        r = m._send_with_retry(flaky, base_delay=0)
    finally:
        m.time.sleep = orig
    assert r.status_code == 200 and calls["n"] == 2


def test_send_with_retry_gives_up_fast_on_persistent_403():
    """A genuinely locked account (persistent 403) stops at the short 403 budget, not full attempts."""
    import app.image3d as m

    calls = {"n": 0}

    class Resp:
        status_code = 403
        headers: dict = {}

    def locked():
        calls["n"] += 1
        return Resp()

    orig = m.time.sleep
    m.time.sleep = lambda *_: None
    try:
        r = m._send_with_retry(locked, base_delay=0)
    finally:
        m.time.sleep = orig
    assert r.status_code == 403 and calls["n"] == m._FLAKY_403_ATTEMPTS
