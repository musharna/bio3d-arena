# Comprehensive Image-to-3D Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add fal.ai + Replicate aggregator adapters so one key each unlocks ~12-15 competitive image-to-3D generators, ingested as AI-reconstruction entries via the existing pipeline.

**Architecture:** Two new generator functions (`generate_fal`, `generate_replicate`) mirror the existing `generate_tripo` submit→poll→download shape behind injectable transports. The `PROVIDERS` registry grows with entries that bind a model id via `functools.partial`, sharing one env var per aggregator. The `generate_api_recon` ingest loop is unchanged except for provider-aware provenance.

**Tech Stack:** Python, httpx (already a dep), trimesh (test GLB fixtures), base64 (stdlib).

## Global Constraints

- Aggregator-first: `generate_fal`/`generate_replicate` take a `model` param; `PROVIDERS` entries bind it via `functools.partial`, sharing `FAL_KEY` / `REPLICATE_API_TOKEN`. Direct `tripo` (existing) stays.
- API keys NEVER committed/logged/echoed; only in Authorization headers. A provider with no key in env is skipped (existing `generate_api_recon` behavior).
- Both new transports are LIVE BINDINGS — exact fal/Replicate endpoint paths, model ids, status strings, and result-URL fields are VERIFIED against current docs at implementation (fal.ai/docs, replicate.com/docs). Unit tests pin orchestration via FAKE transports (no network). Live runs are key-gated.
- Reuse the `generate(image_bytes, *, api_key, transport=None, timeout_s=300, poll_interval_s=5) -> bytes` contract and the module's `_SUCCESS`/`_FAILED` status sets (status is lowercased before the check).
- No new dependency. Do NOT touch `/home/mjarnold/agrigen`.
- `source = "api:<slug>"` → `source_class` already returns `"ai"` (slug may contain a colon, e.g. `api:fal:trellis` — `startswith("api:")` still holds).

## File Structure

- **Modify** `app/image3d.py` — add `_data_uri`, `generate_fal` + `FalTransport`, `generate_replicate` + `ReplicateTransport`, and grow `PROVIDERS`.
- **Modify** `scripts/generate_api_recon.py` — provider-aware `_provenance(slug, name)`.
- **Create** `tests/test_image3d_aggregators.py` — fake-transport unit tests + registry assertions.
- **Modify** `tests/test_generate_api_recon.py` — provenance test.

---

### Task 1: fal.ai adapter — `generate_fal` + `FalTransport`

**Files:**

- Modify: `app/image3d.py` (add `_data_uri`, `generate_fal`, `FalTransport`)
- Test: `tests/test_image3d_aggregators.py`

**Interfaces:**

- Consumes: module `Image3DError`, `_SUCCESS`, `_FAILED` (existing).
- Produces: `_data_uri(image_bytes: bytes, mime: str = "image/jpeg") -> str`; `generate_fal(image_bytes, *, api_key, model, transport=None, timeout_s=300, poll_interval_s=5) -> bytes`; `FalTransport` with `submit(image_bytes, model, api_key) -> dict`, `poll(req, api_key) -> tuple[str, str | None]`, `download(url) -> bytes`.

- [ ] **Step 1: Write the failing tests** (`tests/test_image3d_aggregators.py`)

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_image3d_aggregators.py -v`
Expected: FAIL — `ImportError: cannot import name 'generate_fal'`

- [ ] **Step 3: Implement in `app/image3d.py`** (add after `generate_tripo`/`TripoTransport`, before `PROVIDERS`)

```python
import base64  # add to the existing imports at the top of the file


def _data_uri(image_bytes: bytes, mime: str = "image/jpeg") -> str:
    """Inline an image as a data URI for APIs that take an image_url string."""
    return f"data:{mime};base64," + base64.b64encode(image_bytes).decode("ascii")


def generate_fal(
    image_bytes: bytes,
    *,
    api_key: str,
    model: str,
    transport=None,
    timeout_s: int = 300,
    poll_interval_s: int = 5,
) -> bytes:
    """fal.ai image->3D for a given model id: submit → poll → download GLB."""
    t = transport or FalTransport()
    req = t.submit(image_bytes, model, api_key)
    waited = 0
    while True:
        status, glb_url = t.poll(req, api_key)
        s = (status or "").lower()
        if s in _SUCCESS:
            if not glb_url:
                raise Image3DError(f"fal {model}: completed but no model url")
            break
        if s in _FAILED:
            raise Image3DError(f"fal {model}: {status}")
        if waited >= timeout_s:
            raise Image3DError(f"fal {model}: timed out after {timeout_s}s")
        time.sleep(poll_interval_s)
        waited += poll_interval_s
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

    def submit(self, image_bytes: bytes, model: str, api_key: str) -> dict:
        r = self._client.post(
            f"{self.BASE}/{model}",
            headers=self._hdr(api_key),
            json={"input": {"image_url": _data_uri(image_bytes)}},
        )
        r.raise_for_status()
        return r.json()  # {request_id, status_url, response_url}

    def poll(self, req: dict, api_key: str) -> tuple[str, str | None]:
        r = self._client.get(req["status_url"], headers=self._hdr(api_key))
        r.raise_for_status()
        status = r.json().get("status", "")
        if status.lower() not in _SUCCESS:
            return status, None
        res = self._client.get(req["response_url"], headers=self._hdr(api_key))
        res.raise_for_status()
        d = res.json()
        mesh = d.get("model_mesh") or d.get("mesh") or {}
        return status, (mesh.get("url") if isinstance(mesh, dict) else mesh)

    def download(self, url: str) -> bytes:
        r = self._client.get(url)
        r.raise_for_status()
        return r.content
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_image3d_aggregators.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add app/image3d.py tests/test_image3d_aggregators.py
git commit -m "feat(api-gen): fal.ai aggregator adapter (generate_fal, injectable transport)"
```

---

### Task 2: Replicate adapter — `generate_replicate` + `ReplicateTransport`

**Files:**

- Modify: `app/image3d.py` (add `generate_replicate`, `ReplicateTransport`)
- Test: `tests/test_image3d_aggregators.py`

**Interfaces:**

- Consumes: `Image3DError`, `_SUCCESS`, `_FAILED`, `_data_uri` (Task 1).
- Produces: `generate_replicate(image_bytes, *, api_key, model, transport=None, timeout_s=300, poll_interval_s=5) -> bytes`; `ReplicateTransport` with `submit(image_bytes, model, api_key) -> dict`, `poll(req, api_key) -> tuple[str, str | None]`, `download(url) -> bytes`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_image3d_aggregators.py`)

```python
from app.image3d import generate_replicate


class FakeReplicateTransport:
    def __init__(self, poll_statuses, glb_url, glb):
        self._statuses = list(poll_statuses)
        self._glb_url = glb_url
        self._glb = glb
        self.calls = []

    def submit(self, image_bytes, model, api_key):
        self.calls.append(("submit", model))
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
    out = generate_replicate(b"img", api_key="k", model="firtoz/trellis", transport=t, poll_interval_s=0)
    assert out == glb
    assert t.calls == [("submit", "firtoz/trellis"), "poll", "poll", "download"]


def test_generate_replicate_raises_on_failed():
    t = FakeReplicateTransport(["failed"], "https://rep/x.glb", _box_glb())
    with pytest.raises(Image3DError):
        generate_replicate(b"img", api_key="k", model="m", transport=t, poll_interval_s=0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_image3d_aggregators.py -v`
Expected: FAIL — `ImportError: cannot import name 'generate_replicate'`

- [ ] **Step 3: Implement in `app/image3d.py`** (add after `FalTransport`, before `PROVIDERS`)

```python
def generate_replicate(
    image_bytes: bytes,
    *,
    api_key: str,
    model: str,
    transport=None,
    timeout_s: int = 300,
    poll_interval_s: int = 5,
) -> bytes:
    """Replicate image->3D for a given model: create prediction → poll → download GLB."""
    t = transport or ReplicateTransport()
    req = t.submit(image_bytes, model, api_key)
    waited = 0
    while True:
        status, glb_url = t.poll(req, api_key)
        s = (status or "").lower()
        if s in _SUCCESS:
            if not glb_url:
                raise Image3DError(f"replicate {model}: succeeded but no model url")
            break
        if s in _FAILED:
            raise Image3DError(f"replicate {model}: {status}")
        if waited >= timeout_s:
            raise Image3DError(f"replicate {model}: timed out after {timeout_s}s")
        time.sleep(poll_interval_s)
        waited += poll_interval_s
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

    def submit(self, image_bytes: bytes, model: str, api_key: str) -> dict:
        r = self._client.post(
            f"{self.BASE}/models/{model}/predictions",
            headers=self._hdr(api_key),
            json={"input": {"image": _data_uri(image_bytes)}},
        )
        r.raise_for_status()
        d = r.json()
        return {"get_url": (d.get("urls") or {}).get("get")}

    def poll(self, req: dict, api_key: str) -> tuple[str, str | None]:
        r = self._client.get(req["get_url"], headers=self._hdr(api_key))
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
        r = self._client.get(url)
        r.raise_for_status()
        return r.content
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_image3d_aggregators.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add app/image3d.py tests/test_image3d_aggregators.py
git commit -m "feat(api-gen): Replicate aggregator adapter (generate_replicate, injectable transport)"
```

---

### Task 3: PROVIDERS catalog + provider-aware provenance

**Files:**

- Modify: `app/image3d.py` (grow `PROVIDERS`)
- Modify: `scripts/generate_api_recon.py` (`_provenance` helper)
- Test: `tests/test_image3d_aggregators.py`, `tests/test_generate_api_recon.py`

**Interfaces:**

- Consumes: `generate_fal`, `generate_replicate` (Tasks 1-2).
- Produces: `PROVIDERS` grown with `fal:*` (env `FAL_KEY`) + `replicate:*` (env `REPLICATE_API_TOKEN`) entries; `_provenance(slug, name) -> tuple[str, str]` (license, external_url) in the adapter.

- [ ] **Step 1: Write the failing registry + provenance tests** (append to `tests/test_image3d_aggregators.py`)

```python
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
```

And in `tests/test_generate_api_recon.py` (append):

```python
def test_provenance_by_slug_prefix():
    from scripts.generate_api_recon import _provenance

    assert _provenance("fal:trellis", "TRELLIS (fal)")[1] == "https://fal.ai"
    assert _provenance("replicate:trellis", "TRELLIS (Replicate)")[1] == "https://replicate.com"
    assert _provenance("tripo", "Tripo")[1] == "https://platform.tripo3d.ai"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_image3d_aggregators.py::test_providers_registry_catalog tests/test_generate_api_recon.py::test_provenance_by_slug_prefix -v`
Expected: FAIL — only `tripo` in PROVIDERS; no `_provenance`.

- [ ] **Step 3: Grow `PROVIDERS`** in `app/image3d.py` — replace the existing registry with:

```python
import functools  # add to imports at top of file

PROVIDERS: dict[str, tuple] = {
    "tripo": (generate_tripo, "TRIPO_API_KEY", "Tripo"),
    # fal.ai (one FAL_KEY) — verify exact model paths at impl against fal.ai/3d-models
    "fal:hunyuan3d-v2": (functools.partial(generate_fal, model="fal-ai/hunyuan3d/v2"), "FAL_KEY", "Hunyuan3D v2 (fal)"),
    "fal:hunyuan3d-v3": (functools.partial(generate_fal, model="fal-ai/hunyuan3d-v3/image-to-3d"), "FAL_KEY", "Hunyuan3D v3 (fal)"),
    "fal:trellis": (functools.partial(generate_fal, model="fal-ai/trellis"), "FAL_KEY", "TRELLIS (fal)"),
    "fal:triposr": (functools.partial(generate_fal, model="fal-ai/triposr"), "FAL_KEY", "TripoSR (fal)"),
    "fal:hyper3d": (functools.partial(generate_fal, model="fal-ai/hyper3d/rodin"), "FAL_KEY", "Rodin/Hyper3D (fal)"),
    # Replicate (one REPLICATE_API_TOKEN) — verify exact model ids/versions at impl
    "replicate:hunyuan3d-3.1": (functools.partial(generate_replicate, model="tencent/hunyuan-3d-3.1"), "REPLICATE_API_TOKEN", "Hunyuan3D 3.1 (Replicate)"),
    "replicate:trellis": (functools.partial(generate_replicate, model="firtoz/trellis"), "REPLICATE_API_TOKEN", "TRELLIS (Replicate)"),
    "replicate:trellis2": (functools.partial(generate_replicate, model="fishwowater/trellis2"), "REPLICATE_API_TOKEN", "TRELLIS 2 (Replicate)"),
    "replicate:rodin": (functools.partial(generate_replicate, model="hyper3d/rodin"), "REPLICATE_API_TOKEN", "Rodin (Replicate)"),
}
```

- [ ] **Step 4: Add `_provenance` to `scripts/generate_api_recon.py`** and use it. Add this helper near the top (after the constants):

```python
def _provenance(slug: str, name: str) -> tuple[str, str]:
    """(license, external_url) for an api: provider, derived from the slug prefix."""
    if slug.startswith("fal:"):
        url = "https://fal.ai"
    elif slug.startswith("replicate:"):
        url = "https://replicate.com"
    elif slug == "tripo":
        url = "https://platform.tripo3d.ai"
    else:
        url = ""
    return f"{name} generated-asset terms (see provider)", url
```

Then in `generate_api_recon`, replace the provenance block:

```python
            out.source = f"api:{slug}"
            out.license = f"{name} generated-asset terms"
            out.attribution = f"Generated by {name} from CC reference photo"
            out.external_url = "https://platform.tripo3d.ai/" if slug == "tripo" else ""
```

with:

```python
            out.source = f"api:{slug}"
            out.license, out.external_url = _provenance(slug, name)
            out.attribution = f"Generated by {name} from CC reference photo"
```

- [ ] **Step 5: Run the tests + full suite**

Run: `.venv/bin/python -m pytest tests/test_image3d_aggregators.py tests/test_generate_api_recon.py -v`
Expected: PASS. Then `.venv/bin/python -m pytest -q` → all pass.

- [ ] **Step 6: Commit**

```bash
git add app/image3d.py scripts/generate_api_recon.py tests/test_image3d_aggregators.py tests/test_generate_api_recon.py
git commit -m "feat(api-gen): full fal+replicate PROVIDERS catalog + provider-aware provenance"
```

---

## Out of scope (operational, not a build task)

- The LIVE multi-provider run is KEY-GATED: set `FAL_KEY` and/or `REPLICATE_API_TOKEN` (and `TRIPO_API_KEY`), then `scripts/generate_api_recon.py` generates across every keyed provider. Verify each transport's exact endpoint/field names against fal.ai + replicate.com docs at that point, then render + independent-critic gate.
- Direct first-party APIs (Meshy/Rodin direct), a per-generator leaderboard, and cost/batching controls.

## Notes for the implementer

- The `model` kwarg is bound out of the PROVIDERS callable via `functools.partial`, so the adapter's `fn(image, api_key=key)` call still works — do not change `generate_api_recon`'s call site.
- Reuse the module's existing `_SUCCESS`/`_FAILED` sets; lowercase the provider status before checking (fal returns `COMPLETED`, Replicate returns `succeeded`/`failed`/`processing`).
- Verify exact fal/Replicate request+response shapes against current docs when wiring the live run; the fake-transport unit tests pin only the orchestration contract.
- Keys appear only in `Authorization` headers; never print them.
