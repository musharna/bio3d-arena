"""Image-to-3D provider clients. A provider exposes
`generate(image_bytes, *, api_key, transport=None, timeout_s, poll_interval_s) -> bytes`
(returns GLB), encapsulating that provider's submit->poll->download job flow.

The `transport` is an injectable seam so unit tests drive the state machine without
network; the default real transport is the LIVE BINDING, exercised only by the
key-gated real-execution test. API keys are passed in (from env at the call site) and
are NEVER logged here.
"""

from __future__ import annotations

import time

import httpx


class Image3DError(Exception):
    """Provider error, poll timeout, or empty result from an image-to-3D API."""


# Tripo task statuses (verify exact spellings against platform.tripo3d.ai/docs at impl).
_SUCCESS = {"success", "succeeded", "completed"}
_FAILED = {"failed", "error", "cancelled", "canceled", "banned", "expired"}


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


# slug -> (generate fn, env-var name, display name). Adding Meshy later = one entry + one fn.
PROVIDERS: dict[str, tuple] = {
    "tripo": (generate_tripo, "TRIPO_API_KEY", "Tripo"),
}
