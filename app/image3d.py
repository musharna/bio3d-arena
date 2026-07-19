"""Image-to-3D provider clients. A provider exposes
`generate(image_bytes, *, api_key, transport=None, timeout_s, poll_interval_s) -> bytes`
(returns GLB), encapsulating that provider's submit->poll->download job flow.

The `transport` is an injectable seam so unit tests drive the state machine without
network; the default real transport is the LIVE BINDING, exercised only by the
key-gated real-execution test. API keys are passed in (from env at the call site) and
are NEVER logged here.
"""

from __future__ import annotations

import base64
import functools
import time

import httpx


class Image3DError(Exception):
    """Provider error, poll timeout, or empty result from an image-to-3D API."""


# Tripo task statuses (verify exact spellings against platform.tripo3d.ai/docs at impl).
_SUCCESS = {"success", "succeeded", "completed"}
_FAILED = {"failed", "error", "cancelled", "canceled", "banned", "expired"}

# Transient HTTP failures worth retrying: provider rate limits + gateway errors.
_RETRY_STATUS = {429, 500, 502, 503, 504}
# fal returns a transient 403 ("User is locked") that flaps for a few seconds right after a
# balance top-up, then self-heals — retry it a FEW times only (a genuine exhausted balance keeps
# returning 403 and should fail fast, not retry the full budget).
_FLAKY_403_ATTEMPTS = 3


def _send_with_retry(call, *, attempts: int = 5, base_delay: float = 2.0):
    """Run an HTTP thunk with exponential backoff over transient failures.

    Retries on network/TLS blips (httpx.TransportError — the intermittent SSL "bad record mac"
    seen against fal/replicate), on retryable status codes (429 rate-limit, 5xx), and on a
    short budget of 403s (fal's post-top-up lock flap), honouring a numeric Retry-After when
    present. Non-retryable responses are returned as-is for the caller's own raise_for_status.
    Bounded so a persistently-down (or genuinely locked) provider still fails loudly.
    """
    last_exc = None
    for i in range(attempts):
        try:
            resp = call()
        except httpx.TransportError as e:  # connection reset / SSL alert / read error
            last_exc = e
            if i == attempts - 1:
                raise
            time.sleep(base_delay * (2**i))
            continue
        code = getattr(resp, "status_code", 200)
        retryable = code in _RETRY_STATUS or (code == 403 and i < _FLAKY_403_ATTEMPTS - 1)
        if retryable and i < attempts - 1:
            ra = resp.headers.get("Retry-After") if hasattr(resp, "headers") else None
            delay = float(ra) if (ra and str(ra).isdigit()) else base_delay * (2**i)
            time.sleep(delay)
            continue
        return resp
    if last_exc is not None:  # pragma: no cover — loop returns or raises before here
        raise last_exc
    raise Image3DError("retry loop exhausted without a response")


def generate_tripo(
    image_bytes: bytes,
    *,
    api_key: str,
    transport=None,
    timeout_s: int = 300,
    poll_interval_s: int = 5,
) -> bytes:
    """Tripo image->3D: upload image -> create image_to_model task -> poll -> download GLB."""
    t = transport or TripoTransport()
    file_token = t.upload(image_bytes, api_key)
    task_id = t.create_task(file_token, api_key)
    waited = 0
    while True:
        status, model_url = t.poll(task_id, api_key)
        if status in _SUCCESS:
            if not model_url:
                raise Image3DError("tripo: success but no model url")
            break
        if status in _FAILED:
            raise Image3DError(f"tripo task {task_id} ended: {status}")
        if waited >= timeout_s:
            raise Image3DError(f"tripo task {task_id} timed out after {timeout_s}s")
        time.sleep(poll_interval_s)
        waited += poll_interval_s
    glb = t.download(model_url)
    if not glb:
        raise Image3DError("tripo: empty model download")
    return glb


def _ok(resp: httpx.Response) -> dict:
    resp.raise_for_status()
    body = resp.json()
    if body.get("code") != 0:
        raise Image3DError(f"tripo error {body.get('code')}: {body.get('message')}")
    return body["data"]


class TripoTransport:
    """Real Tripo API transport (LIVE BINDING — verify exact field names/paths against
    https://platform.tripo3d.ai/docs at implementation; only the key-gated real test runs
    this). Flow: POST /upload -> file_token; POST /task type=image_to_model -> task_id;
    GET /task/{id} -> (status, model_url); GET model_url -> GLB bytes. Auth: Bearer key.
    Success envelope: {"code":0,"data":{...}}."""

    BASE = "https://api.tripo3d.ai/v2/openapi"

    def __init__(self, client: httpx.Client | None = None):
        self._client = client or httpx.Client(timeout=60)

    def _hdr(self, api_key: str) -> dict:
        return {"Authorization": f"Bearer {api_key}"}

    def upload(self, image_bytes: bytes, api_key: str) -> str:
        r = self._client.post(
            f"{self.BASE}/upload",
            headers=self._hdr(api_key),
            files={"file": ("ref.jpg", image_bytes, "image/jpeg")},
        )
        d = _ok(r)
        return d.get("image_token") or d["file_token"]

    def create_task(self, file_token: str, api_key: str) -> str:
        r = self._client.post(
            f"{self.BASE}/task",
            headers=self._hdr(api_key),
            json={"type": "image_to_model", "file": {"type": "jpg", "file_token": file_token}},
        )
        return _ok(r)["task_id"]

    def poll(self, task_id: str, api_key: str) -> tuple[str, str | None]:
        r = self._client.get(f"{self.BASE}/task/{task_id}", headers=self._hdr(api_key))
        d = _ok(r)
        output = d.get("output") or {}
        url = output.get("pbr_model") or output.get("model") or output.get("base_model")
        return d.get("status", ""), url

    def download(self, url: str) -> bytes:
        r = self._client.get(url)
        r.raise_for_status()
        return r.content


def _data_uri(image_bytes: bytes, mime: str | None = None) -> str:
    """Inline an image as a data URI for APIs that take an image_url string.

    Sniffs PNG vs JPEG from the magic bytes (multi-view view sets may be PNG) so the declared
    mime matches the payload; falls back to image/jpeg.
    """
    if mime is None:
        mime = "image/png" if image_bytes[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(image_bytes).decode("ascii")


def _downscale_image(image_bytes: bytes, *, max_dim: int = 1024, quality: int = 88) -> bytes:
    """Downscale an image to fit max_dim and re-encode as JPEG.

    Reference photos can be several MB; inlined as a base64 data URI they produce multi-MB
    request bodies that providers reject (fal returns 422) and that trip TLS resets mid-upload.
    Image-to-3D models work from ~1024px, so shrink first. Returns the original bytes unchanged
    if Pillow cannot decode them (don't fail a generation over a thumbnailing edge case).
    """
    import io

    from PIL import Image

    try:
        im = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    except Exception:  # noqa: BLE001 — undecodable input: pass through rather than abort the job
        return image_bytes
    if max(im.size) > max_dim:
        im.thumbnail((max_dim, max_dim))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def _image_data_uri(image_bytes: bytes) -> str:
    """Downscale then inline an image as a data URI (the size-safe path for image inputs)."""
    return _data_uri(_downscale_image(image_bytes))


def generate_fal(
    source,
    *,
    api_key: str,
    model: str,
    mode: str = "image",
    image_field: str = "image_url",
    extra_input: dict | None = None,
    transport=None,
    timeout_s: int = 300,
    poll_interval_s: int = 5,
) -> bytes:
    """fal.ai →3D for a given model id: submit → poll → download GLB.

    `source` is image bytes when mode="image" (default), a text prompt (str) when mode="text"
    (text→3D), or a list of view-image bytes when mode="multiview" (multi-view reconstruction).
    Text→3D outputs are organ/part-level, not faithful whole plants.

    `image_field` is the model's image input key — it differs per fal model (trellis/triposr use
    `image_url`; hunyuan3d uses `input_image_url`; rodin uses `input_image_urls` (a list)). A
    field name ending in "urls" is sent as a single-element list.

    `extra_input` merges model-specific input params into the request (e.g. hunyuan3d/v2 needs
    `textured_mesh=True` or it returns an untextured white mesh).
    """
    t = transport or FalTransport()
    req = t.submit(source, model, api_key, mode, image_field, extra_input)
    start = time.monotonic()  # wall-clock budget: counts retry/backoff time inside poll(), too
    while True:
        status, glb_url = t.poll(req, api_key)
        s = (status or "").lower()
        if s in _SUCCESS:
            if not glb_url:
                raise Image3DError(f"fal {model}: completed but no model url")
            break
        if s in _FAILED:
            raise Image3DError(f"fal {model}: {status}")
        if time.monotonic() - start >= timeout_s:
            raise Image3DError(f"fal {model}: timed out after {timeout_s}s")
        time.sleep(poll_interval_s)
    glb = t.download(glb_url)
    if not glb:
        raise Image3DError(f"fal {model}: empty download")
    return glb


class FalTransport:
    """Real fal.ai queue transport (LIVE BINDING — verify exact paths/fields against
    fal.ai/docs at impl; only the key-gated run exercises it). submit POSTs the image to the
    model's queue endpoint → request handle; poll returns (status, glb_url-when-COMPLETED);
    download fetches the GLB. Auth: `Authorization: Key <api_key>`."""

    BASE = "https://queue.fal.run"

    def __init__(self, client: httpx.Client | None = None):
        self._client = client or httpx.Client(timeout=60)

    def _hdr(self, api_key: str) -> dict:
        return {"Authorization": f"Key {api_key}"}

    def submit(
        self,
        source,
        model: str,
        api_key: str,
        mode: str = "image",
        image_field: str = "image_url",
        extra_input: dict | None = None,
    ) -> dict:
        inp: dict
        if mode == "text":
            inp = {"prompt": source}
        elif mode == "multiview":  # source is a list of view-image bytes
            field = image_field if image_field.endswith("urls") else "image_urls"
            inp = {field: [_image_data_uri(v) for v in source]}
        elif image_field.endswith("urls"):  # list-typed image field (e.g. rodin input_image_urls)
            inp = {image_field: [_image_data_uri(source)]}
        else:
            inp = {image_field: _image_data_uri(source)}
        if extra_input:  # model-specific params (e.g. hunyuan3d/v2 textured_mesh=True)
            inp.update(extra_input)
        # The fal queue endpoint takes the input fields at the body ROOT — NOT wrapped in
        # {"input": ...} (a wrapper yields 422 "image_url field required").
        r = _send_with_retry(
            lambda: self._client.post(f"{self.BASE}/{model}", headers=self._hdr(api_key), json=inp)
        )
        r.raise_for_status()
        return r.json()  # {request_id, status_url, response_url}

    def poll(self, req: dict, api_key: str) -> tuple[str, str | None]:
        if not req.get("status_url"):  # malformed submit response → Image3DError, not KeyError
            raise Image3DError("fal: no status_url in submit response")
        r = _send_with_retry(
            lambda: self._client.get(req["status_url"], headers=self._hdr(api_key))
        )
        r.raise_for_status()
        status = r.json().get("status", "")
        if status.lower() not in _SUCCESS:
            return status, None
        res = _send_with_retry(
            lambda: self._client.get(req["response_url"], headers=self._hdr(api_key))
        )
        res.raise_for_status()
        d = res.json()
        # output key varies by model: image→3D uses model_mesh; some text→3D use model_glb.
        mesh = d.get("model_mesh") or d.get("model_glb") or d.get("mesh") or {}
        return status, (mesh.get("url") if isinstance(mesh, dict) else mesh)

    def download(self, url: str) -> bytes:
        r = _send_with_retry(lambda: self._client.get(url))
        r.raise_for_status()
        return r.content


def generate_replicate(
    source,
    *,
    api_key: str,
    model: str,
    mode: str = "image",
    extra_input: dict | None = None,
    transport=None,
    timeout_s: int = 300,
    poll_interval_s: int = 5,
) -> bytes:
    """Replicate →3D for a given model: create prediction → poll → download GLB.

    `source` is image bytes when mode="image" (default), or a text prompt (str) when mode="text".
    `extra_input` merges model-specific params into the prediction input (e.g. Shap-E needs
    `output_type="mesh"` — its default output is a rotating GIF, not a mesh), mirroring generate_fal.
    """
    t = transport or ReplicateTransport()
    req = t.submit(source, model, api_key, mode, extra_input)
    start = time.monotonic()  # wall-clock budget: counts retry/backoff time inside poll(), too
    while True:
        status, glb_url = t.poll(req, api_key)
        s = (status or "").lower()
        if s in _SUCCESS:
            if not glb_url:
                raise Image3DError(f"replicate {model}: succeeded but no model url")
            break
        if s in _FAILED:
            raise Image3DError(f"replicate {model}: {status}")
        if time.monotonic() - start >= timeout_s:
            raise Image3DError(f"replicate {model}: timed out after {timeout_s}s")
        time.sleep(poll_interval_s)
    glb = t.download(glb_url)
    if not glb:
        raise Image3DError(f"replicate {model}: empty download")
    return glb


class ReplicateTransport:
    """Real Replicate predictions transport (LIVE BINDING — verify against replicate.com/docs
    at impl; only the key-gated run exercises it). submit creates a prediction for the model
    with the image input; poll returns (status, glb_url-when-succeeded); download fetches it.
    Auth: `Authorization: Bearer <api_key>`."""

    BASE = "https://api.replicate.com/v1"

    def __init__(self, client: httpx.Client | None = None):
        self._client = client or httpx.Client(timeout=60)

    def _hdr(self, api_key: str) -> dict:
        return {"Authorization": f"Bearer {api_key}"}

    # image input field name varies by model (TRELLIS=images[list], Hunyuan/Rodin=image, …);
    # resolved from the model's input schema rather than hard-coded.
    _IMAGE_FIELDS = ("images", "image", "image_url", "input_image")

    def submit(
        self, source, model: str, api_key: str, mode: str = "image", extra_input: dict | None = None
    ) -> dict:
        # Community models require the version-pinned /predictions endpoint;
        # /models/{owner}/{name}/predictions 404s for them (official models only).
        m = _send_with_retry(
            lambda: self._client.get(f"{self.BASE}/models/{model}", headers=self._hdr(api_key))
        )
        m.raise_for_status()
        md = m.json()
        ver = md.get("latest_version") or {}
        version = ver.get("id")
        if not version:
            raise Image3DError(f"replicate {model}: no latest_version to pin")
        inp: dict[str, object]
        if mode == "text":
            inp = {"prompt": source}
        else:
            props = (
                (((ver.get("openapi_schema") or {}).get("components") or {}).get("schemas") or {})
                .get("Input", {})
                .get("properties", {})
            )
            field = next((f for f in self._IMAGE_FIELDS if f in props), None)
            if field is None:
                raise Image3DError(
                    f"replicate {model}: no known image input field in schema {list(props)}"
                )
            uri = _image_data_uri(source)
            inp = {field: [uri]} if field == "images" else {field: uri}
            # Some models gate the GLB export behind a flag (TRELLIS: generate_model defaults
            # False → only video/gaussian, no mesh). We want the mesh, so opt in when offered.
            if "generate_model" in props:
                inp["generate_model"] = True
        if extra_input:  # model-specific params (e.g. shap-e output_type="mesh")
            inp.update(extra_input)
        r = _send_with_retry(
            lambda: self._client.post(
                f"{self.BASE}/predictions",
                headers=self._hdr(api_key),
                json={"version": version, "input": inp},
            )
        )
        r.raise_for_status()
        d = r.json()
        return {"get_url": (d.get("urls") or {}).get("get")}

    def poll(self, req: dict, api_key: str) -> tuple[str, str | None]:
        if not req.get("get_url"):  # malformed submit response → surface as Image3DError
            raise Image3DError("replicate: no prediction poll url in submit response")
        r = _send_with_retry(lambda: self._client.get(req["get_url"], headers=self._hdr(api_key)))
        r.raise_for_status()
        d = r.json()
        status = d.get("status", "")
        if status.lower() not in _SUCCESS:
            return status, None
        out = d.get("output")
        # output may be a GLB url string, a list, or a dict with a mesh/glb url.
        if isinstance(out, str):
            url = out
        elif isinstance(out, list):
            url = out[-1] if out else None
        elif isinstance(out, dict):
            url = out.get("mesh") or out.get("glb") or out.get("model_file")
        else:
            url = None
        return status, url

    def download(self, url: str) -> bytes:
        r = _send_with_retry(lambda: self._client.get(url))
        r.raise_for_status()
        return r.content


# slug -> (generate fn, env-var name, display name). Adding Meshy later = one entry + one fn.
PROVIDERS: dict[str, tuple] = {
    "tripo": (generate_tripo, "TRIPO_API_KEY", "Tripo"),
    # fal.ai (one FAL_KEY) — verify exact model paths at impl against fal.ai/3d-models
    "fal:hunyuan3d-v2": (
        functools.partial(
            generate_fal,
            model="fal-ai/hunyuan3d/v2",
            image_field="input_image_url",
            extra_input={"textured_mesh": True},  # else returns an untextured white mesh
        ),
        "FAL_KEY",
        "Hunyuan3D v2 (fal)",
    ),
    "fal:hunyuan3d-v3": (
        functools.partial(
            generate_fal, model="fal-ai/hunyuan3d-v3/image-to-3d", image_field="input_image_url"
        ),
        "FAL_KEY",
        "Hunyuan3D v3 (fal)",
    ),
    "fal:trellis": (
        functools.partial(generate_fal, model="fal-ai/trellis"),
        "FAL_KEY",
        "TRELLIS (fal)",
    ),
    "fal:triposr": (
        functools.partial(generate_fal, model="fal-ai/triposr"),
        "FAL_KEY",
        "TripoSR (fal)",
    ),
    "fal:hyper3d": (
        # Rodin cold-boots can exceed the 300s default; give it a longer wall-clock budget.
        functools.partial(
            generate_fal,
            model="fal-ai/hyper3d/rodin",
            image_field="input_image_urls",
            timeout_s=600,
        ),
        "FAL_KEY",
        "Rodin/Hyper3D (fal)",
    ),
    # Newer image→3D models (2026 catalog). Input field / output key verified live per model at
    # impl (fal schemas differ); default image_field="image_url" unless a probe showed otherwise.
    "fal:meshy-v6": (
        functools.partial(generate_fal, model="fal-ai/meshy/v6/image-to-3d", timeout_s=600),
        "FAL_KEY",
        "Meshy 6 (fal)",
    ),
    "fal:sam-3d": (
        functools.partial(generate_fal, model="fal-ai/sam-3/3d-objects", timeout_s=600),
        "FAL_KEY",
        "SAM 3D (fal)",
    ),
    "fal:pixal3d": (
        functools.partial(generate_fal, model="fal-ai/pixal3d", timeout_s=600),
        "FAL_KEY",
        "Pixal3D (fal)",
    ),
    # Replicate (one REPLICATE_API_TOKEN) — verify exact model ids/versions at impl
    "replicate:hunyuan3d-3.1": (
        functools.partial(generate_replicate, model="tencent/hunyuan-3d-3.1"),
        "REPLICATE_API_TOKEN",
        "Hunyuan3D 3.1 (Replicate)",
    ),
    "replicate:trellis": (
        functools.partial(generate_replicate, model="firtoz/trellis"),
        "REPLICATE_API_TOKEN",
        "TRELLIS (Replicate)",
    ),
    "replicate:trellis2": (
        # TRELLIS 2 is slow (>300s); give it a longer budget so it isn't abandoned mid-run.
        functools.partial(generate_replicate, model="fishwowater/trellis2", timeout_s=900),
        "REPLICATE_API_TOKEN",
        "TRELLIS 2 (Replicate)",
    ),
    "replicate:rodin": (
        # Rodin on Replicate requires a text `prompt` (image alone → 422); the image-only recon
        # path can't supply one, and Rodin is already covered by fal:hyper3d. Kept in the catalog
        # but excluded from the image bake-off (PROMPT_REQUIRED).
        functools.partial(generate_replicate, model="hyper3d/rodin"),
        "REPLICATE_API_TOKEN",
        "Rodin (Replicate)",
    ),
}


# Text→3D providers (a text PROMPT instead of an image; mode="text"). These produce ORGAN/part-level
# meshes, NOT faithful whole plants — the generative-3D baseline, flagged modality=text on ingest.
# Output key varies per model (model_glb vs model_mesh) — handled in the transports' poll.
# Verify exact model paths/output shapes at the key-gated live run.
TEXT_PROVIDERS: dict[str, tuple] = {
    "fal:hunyuan3d-v3-text": (
        functools.partial(generate_fal, model="fal-ai/hunyuan3d-v3/text-to-3d", mode="text"),
        "FAL_KEY",
        "Hunyuan3D v3 text (fal)",
    ),
    "fal:tripo-p1-text": (
        functools.partial(generate_fal, model="tripo3d/p1/text-to-3d", mode="text"),
        "FAL_KEY",
        "Tripo P1 text (fal)",
    ),
    "fal:tripo-h31-text": (
        functools.partial(generate_fal, model="tripo3d/h3.1/text-to-3d", mode="text"),
        "FAL_KEY",
        "Tripo H3.1 text (fal)",
    ),
    "fal:rodin-text": (
        functools.partial(generate_fal, model="fal-ai/hyper3d/rodin", mode="text"),
        "FAL_KEY",
        "Rodin text (fal)",
    ),
    "replicate:hunyuan3d-3.1-text": (
        functools.partial(generate_replicate, model="tencent/hunyuan-3d-3.1", mode="text"),
        "REPLICATE_API_TOKEN",
        "Hunyuan3D 3.1 text (Replicate)",
    ),
    "replicate:rodin-text": (
        functools.partial(generate_replicate, model="hyper3d/rodin", mode="text"),
        "REPLICATE_API_TOKEN",
        "Rodin text (Replicate)",
    ),
    # Deepening additions (web-verified 2026). Meshy v6 is a current SOTA text→3D on fal (prompt
    # input, model_glb.url output — handled by FalTransport.poll); the non-preview endpoint is the
    # textured one, so no extra params. Verify the exact fal field contract at the key-gated run.
    "fal:meshy-v6-text": (
        functools.partial(generate_fal, model="fal-ai/meshy/v6/text-to-3d", mode="text"),
        "FAL_KEY",
        "Meshy v6 text (fal)",
    ),
    # DEFERRED — Shap-E (cjwbw/shap-e, Replicate) is a genuine text→3D but the key-gated validation
    # run showed it returns a non-GLB mesh (native .ply/.obj), which ingest rejects ("incorrect
    # header on GLB file"). Re-adding it needs a PLY/OBJ→GLB conversion step (trimesh) + a re-verify
    # run; low priority since it's a low-fidelity 2023 baseline. The generate_replicate `extra_input`
    # plumbing below stays — that conversion-based re-add would use it for output_type="mesh".
}


# Multi-view reconstruction providers (mode="multiview"): feed N views → feed-forward MV→mesh GLB.
# The "compose synthetic views → 3D" path — COLMAP fails to pose AI-generated views, so we skip SfM
# and feed views directly to a feed-forward model. fn is called as fn(list_of_view_bytes, api_key=).
# Verify exact endpoint paths/output shapes at the key-gated live run.
MULTIVIEW_PROVIDERS: dict[str, tuple] = {
    "recon:trellis-mv": (
        functools.partial(generate_fal, model="fal-ai/trellis/multi", mode="multiview"),
        "FAL_KEY",
        "TRELLIS multi-view (fal)",
    ),
    "recon:hunyuan3d-v2-mv": (
        functools.partial(generate_fal, model="fal-ai/hunyuan3d/v2/multi-view", mode="multiview"),
        "FAL_KEY",
        "Hunyuan3D v2 multi-view (fal)",
    ),
}


def _normalize_views(outs: list[bytes], n_views: int, grid: tuple[int, int]) -> list[bytes]:
    """NVS output → exactly n_views PNG byte-strings. Accepts either a list of n_views images
    or a single tiled contact sheet (grid = (cols, rows)) which is de-tiled left-to-right,
    top-to-bottom."""
    import io

    from PIL import Image

    if len(outs) >= n_views:
        return outs[:n_views]
    if len(outs) == 1:
        sheet = Image.open(io.BytesIO(outs[0])).convert("RGB")
        cols, rows = grid
        w, h = sheet.size
        tw, th = w // cols, h // rows
        tiles: list[bytes] = []
        for r in range(rows):
            for c in range(cols):
                crop = sheet.crop((c * tw, r * th, (c + 1) * tw, (r + 1) * th))
                buf = io.BytesIO()
                crop.save(buf, "PNG")
                tiles.append(buf.getvalue())
        if len(tiles) < n_views:
            raise Image3DError(f"NVS sheet de-tiled to {len(tiles)} < {n_views}")
        return tiles[:n_views]
    raise Image3DError(f"NVS returned {len(outs)} outputs; expected {n_views} images or 1 sheet")


def generate_nvs(
    image_bytes: bytes,
    *,
    api_key: str,
    model: str,
    n_views: int = 6,
    grid: tuple[int, int] = (2, 3),
    transport=None,
    timeout_s: int = 300,
    poll_interval_s: int = 5,
) -> list[bytes]:
    """Single image → N novel views via a multi-view-diffusion API (e.g. Zero123++).
    transport.poll(req, api_key) -> (status, outputs|None) where outputs is a list of downloaded
    image bytes (n_views separate images, or 1 tiled sheet)."""
    t = transport or NvsReplicateTransport()
    req = t.submit(image_bytes, model, api_key)
    start = time.monotonic()
    while True:
        status, outs = t.poll(req, api_key)
        s = (status or "").lower()
        if s in _SUCCESS:
            if not outs:
                raise Image3DError(f"NVS {model}: succeeded but no output images")
            break
        if s in _FAILED:
            raise Image3DError(f"NVS {model}: {status}")
        if time.monotonic() - start >= timeout_s:
            raise Image3DError(f"NVS {model}: timed out after {timeout_s}s")
        time.sleep(poll_interval_s)
    return _normalize_views(outs, n_views, grid)


class NvsReplicateTransport:
    """Replicate NVS transport (LIVE BINDING — verify against replicate.com/docs at the key-gated
    run). submit creates a version-pinned prediction with the image input; poll returns
    (status, [downloaded image bytes]) when succeeded. Auth: Bearer."""

    BASE = "https://api.replicate.com/v1"

    def __init__(self, client: httpx.Client | None = None):
        self._client = client or httpx.Client(timeout=60)

    def _hdr(self, api_key: str) -> dict:
        return {"Authorization": f"Bearer {api_key}"}

    def submit(self, image_bytes: bytes, model: str, api_key: str) -> dict:
        m = _send_with_retry(
            lambda: self._client.get(f"{self.BASE}/models/{model}", headers=self._hdr(api_key))
        )
        m.raise_for_status()
        version = (m.json().get("latest_version") or {}).get("id")
        if not version:
            raise Image3DError(f"NVS {model}: no latest_version to pin")
        r = _send_with_retry(
            lambda: self._client.post(
                f"{self.BASE}/predictions",
                headers=self._hdr(api_key),
                json={"version": version, "input": {"image": _image_data_uri(image_bytes)}},
            )
        )
        r.raise_for_status()
        return {"get_url": (r.json().get("urls") or {}).get("get")}

    def poll(self, req: dict, api_key: str) -> tuple[str, list[bytes] | None]:
        if not req.get("get_url"):
            raise Image3DError("NVS: no prediction poll url in submit response")
        r = _send_with_retry(lambda: self._client.get(req["get_url"], headers=self._hdr(api_key)))
        r.raise_for_status()
        d = r.json()
        status = d.get("status", "")
        if status.lower() not in _SUCCESS:
            return status, None
        out = d.get("output")
        urls = [out] if isinstance(out, str) else (out if isinstance(out, list) else [])
        imgs = [_send_with_retry(lambda u=u: self._client.get(u)).content for u in urls if u]
        return status, imgs


NVS_PROVIDERS: dict[str, tuple] = {
    # VERIFIED 2026-06-27 (live e2e): slug valid (version c69c6559a29...); output is a single
    # 2-wide × 3-tall (640×960) contact sheet of 320px tiles → de-tiled with the default grid=(2,3).
    "zero123plusplus": (
        functools.partial(generate_nvs, model="jd7h/zero123plusplus"),
        "REPLICATE_API_TOKEN",
        "Zero123++ (Replicate)",
    ),
}
