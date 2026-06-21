# Image-to-3D API Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate tomato 3D models by feeding one canonical CC-licensed reference photo to the Tripo image-to-3D API, ingesting the results as AI-reconstruction entries in the spotlight.

**Architecture:** A provider-agnostic client (`app/image3d.py`) wraps each API's submit→poll→download job flow behind an injectable transport seam. A generation adapter (`scripts/generate_api_recon.py`) feeds the canonical reference image to every provider whose key is in env, then ingests each GLB via the existing `register_output` + isolated-scoring pattern. `source_class` is extended so `api:*` sources group under "AI reconstruction".

**Tech Stack:** Python, httpx (already a dep), trimesh (already a dep, for test GLB fixtures), FastAPI/Jinja2, SQLAlchemy, the AgriGen recon scorer (`:8077`).

## Global Constraints

- First provider = **Tripo** (`TRIPO_API_KEY`). Client is pluggable; others are follow-on adapters.
- API keys come from **env vars only**; NEVER committed, echoed, or logged. No `bash -x` around key use. A provider with no key in env is skipped.
- Tripo API exact field names are verified against https://platform.tripo3d.ai/docs at implementation — the real `TripoTransport` is the live-binding seam, exercised ONLY by the key-gated real-execution test. The unit tests drive a FAKE transport (no network).
- API recons are AI reconstructions: `source_class` returns `"ai"` for `source.startswith("api:")` (and the existing `"bio3d-arena"`).
- Reuse `register_output` (`app/ingest.py:172`): per-object commit; provenance (`source`/`license`/`attribution`/`external_url`) set AFTER register, committed before scoring. Scoring is isolated/best-effort — a scoring failure never drops a hosted object. AgriGen stays read-only.
- The tomato Task title (verbatim): `Solanum lycopersicum — single-image → 3D reconstruction`.
- The live generation run is KEY-GATED (no key → no-op) and is operational, not a build task.

---

## File Structure

- **Modify** `app/sourcing.py` — `source_class` recognizes `api:*` as `"ai"`.
- **Create** `app/image3d.py` — `Image3DError`, `TripoTransport`, `generate_tripo`, `PROVIDERS`.
- **Create** `tests/test_image3d.py` — fake-transport unit tests for the submit/poll/download state machine.
- **Create** `scripts/generate_api_recon.py` — `generate_api_recon` core + `main()`.
- **Create** `tests/test_generate_api_recon.py` — ingest/provenance/skip/error tests with fake providers.
- **Modify** `app/spotlight.py` — `build_spotlight` resolves `reference_image` via storage; set the tomato `reference_image`.
- **Modify** `tests/test_spotlight_page.py` (or a new test) — reference panel renders when set.

---

### Task 1: `source_class` recognizes API recons as "ai"

**Files:**

- Modify: `app/sourcing.py` (the `source_class` function near the bottom)
- Test: `tests/test_source_class.py`

**Interfaces:**

- Produces: `source_class(source)` returns `"ai"` for `"bio3d-arena"` and any `"api:*"`; `"scan"` for SCAN_SOURCES; `"found"` otherwise.

- [ ] **Step 1: Write the failing test** (append to `tests/test_source_class.py`)

```python
def test_source_class_api_is_ai():
    from app.sourcing import source_class

    assert source_class("api:tripo") == "ai"
    assert source_class("api:meshy") == "ai"
    assert source_class("bio3d-arena") == "ai"  # unchanged
    assert source_class("plant3d") == "scan"  # unchanged
    assert source_class("objaverse") == "found"  # unchanged
    assert source_class(None) == "found"  # unchanged
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_source_class.py::test_source_class_api_is_ai -v`
Expected: FAIL — `source_class("api:tripo")` returns `"found"`, not `"ai"`.

- [ ] **Step 3: Implement** — in `app/sourcing.py`, change `source_class`:

```python
def source_class(source: str | None) -> str:
    """Group key for the spotlight: 'ai' (our recon — local or via an image-to-3D API),
    'scan' (real scan dataset), 'found' (artist repos like Objaverse/Sketchfab)."""
    if source == "bio3d-arena" or (source or "").startswith("api:"):
        return "ai"
    if source in SCAN_SOURCES:
        return "scan"
    return "found"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_source_class.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/sourcing.py tests/test_source_class.py
git commit -m "feat(sourcing): source_class groups api:* recons under AI reconstruction"
```

---

### Task 2: Tripo provider client — `app/image3d.py`

**Files:**

- Create: `app/image3d.py`
- Test: `tests/test_image3d.py`

**Interfaces:**

- Produces:
  - `Image3DError(Exception)`
  - `generate_tripo(image_bytes: bytes, *, api_key: str, transport=None, timeout_s: int = 300, poll_interval_s: int = 5) -> bytes`
  - `TripoTransport` (real httpx transport; the live-binding seam)
  - `PROVIDERS: dict[str, tuple]` — `{"tripo": (generate_tripo, "TRIPO_API_KEY", "Tripo")}`

- [ ] **Step 1: Write the failing tests** (`tests/test_image3d.py`)

```python
import pytest
import trimesh

from app.image3d import Image3DError, generate_tripo


class FakeTransport:
    """Drives generate_tripo's submit->poll->download state machine without network.
    `poll_statuses` is consumed one per poll call (last one repeats)."""

    def __init__(self, poll_statuses, model_url, glb):
        self._statuses = list(poll_statuses)
        self._model_url = model_url
        self._glb = glb
        self.calls = []

    def upload(self, image_bytes, api_key):
        self.calls.append("upload")
        return "file-token-xyz"

    def create_task(self, file_token, api_key):
        self.calls.append("create_task")
        assert file_token == "file-token-xyz"
        return "task-123"

    def poll(self, task_id, api_key):
        self.calls.append("poll")
        status = self._statuses.pop(0) if len(self._statuses) > 1 else self._statuses[0]
        url = self._model_url if status == "success" else None
        return status, url

    def download(self, url):
        self.calls.append("download")
        assert url == self._model_url
        return self._glb


def _box_glb() -> bytes:
    return trimesh.creation.box().export(file_type="glb")


def test_generate_tripo_runs_state_machine_and_returns_glb():
    glb = _box_glb()
    t = FakeTransport(["running", "success"], "https://x/model.glb", glb)
    out = generate_tripo(b"img", api_key="k", transport=t, poll_interval_s=0)
    assert out == glb
    assert t.calls == ["upload", "create_task", "poll", "poll", "download"]


def test_generate_tripo_raises_on_failed_status():
    t = FakeTransport(["failed"], "https://x/model.glb", _box_glb())
    with pytest.raises(Image3DError):
        generate_tripo(b"img", api_key="k", transport=t, poll_interval_s=0)


def test_generate_tripo_times_out():
    t = FakeTransport(["running"], "https://x/model.glb", _box_glb())
    with pytest.raises(Image3DError):
        generate_tripo(b"img", api_key="k", transport=t, timeout_s=0, poll_interval_s=0)


def test_generate_tripo_raises_on_empty_download():
    t = FakeTransport(["success"], "https://x/model.glb", b"")
    with pytest.raises(Image3DError):
        generate_tripo(b"img", api_key="k", transport=t, poll_interval_s=0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_image3d.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.image3d'`

- [ ] **Step 3: Implement `app/image3d.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_image3d.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add app/image3d.py tests/test_image3d.py
git commit -m "feat(api-gen): Tripo image-to-3D client with injectable transport seam"
```

---

### Task 3: Generation adapter — `scripts/generate_api_recon.py`

**Files:**

- Create: `scripts/generate_api_recon.py`
- Test: `tests/test_generate_api_recon.py`

**Interfaces:**

- Consumes: `app.image3d.PROVIDERS` + `Image3DError` (Task 2); `app.ingest.register_output`; `app.sourcing.source_class` (Task 1).
- Produces: `generate_api_recon(db, image_bytes, *, providers, env, score_fn=None, task_title=TOMATO_TITLE) -> dict` with keys `generated`, `skipped_no_key`, `errors`, `by_provider`.

- [ ] **Step 1: Write the failing test** (`tests/test_generate_api_recon.py`)

```python
import json

import trimesh
from sqlalchemy import select

from app import ingest, sourcing
from app.database import SessionLocal, init_db
from app.image3d import Image3DError
from app.models import Category, ModelOutput, Task
from scripts.generate_api_recon import generate_api_recon

TOMATO = "Solanum lycopersicum — single-image → 3D reconstruction"


def setup_module(_m):
    init_db()


def _tomato_task(db):
    cat = db.query(Category).filter_by(slug="plants").first() or Category(
        slug="plants", name="Plants"
    )
    db.add(cat)
    db.flush()
    db.add(Task(category_id=cat.id, title=TOMATO, prompt="p"))
    db.commit()


def _box_glb():
    return trimesh.creation.box().export(file_type="glb")


def test_generate_api_recon_ingests_ai_output():
    db = SessionLocal()
    try:
        _tomato_task(db)
        glb = _box_glb()

        def fake_tripo(image_bytes, *, api_key):
            assert api_key == "secret-key"
            return glb

        providers = {"tripo": (fake_tripo, "TRIPO_API_KEY", "Tripo")}
        report = generate_api_recon(
            db, b"img", providers=providers, env={"TRIPO_API_KEY": "secret-key"}
        )
        assert report["generated"] == 1
        out = db.execute(
            select(ModelOutput).where(ModelOutput.source == "api:tripo")
        ).scalars().one()
        assert sourcing.source_class(out.source) == "ai"
        assert json.loads(out.meta_json)["provider"] == "tripo"
        assert out.attribution and "Tripo" in out.attribution
    finally:
        db.close()


def test_generate_api_recon_skips_provider_without_key():
    db = SessionLocal()
    try:
        _tomato_task(db)
        providers = {"tripo": (lambda *a, **k: b"x", "TRIPO_API_KEY", "Tripo")}
        report = generate_api_recon(db, b"img", providers=providers, env={})
        assert report["skipped_no_key"] == 1
        assert report["generated"] == 0
    finally:
        db.close()


def test_generate_api_recon_counts_provider_error():
    db = SessionLocal()
    try:
        _tomato_task(db)

        def boom(image_bytes, *, api_key):
            raise Image3DError("provider down")

        providers = {"tripo": (boom, "TRIPO_API_KEY", "Tripo")}
        report = generate_api_recon(
            db, b"img", providers=providers, env={"TRIPO_API_KEY": "k"}
        )
        assert report["errors"] == 1
        assert report["generated"] == 0
    finally:
        db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_generate_api_recon.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.generate_api_recon'`

- [ ] **Step 3: Implement `scripts/generate_api_recon.py`**

```python
"""Generate tomato 3D models via image-to-3D APIs from the canonical reference photo, and
ingest them as AI-reconstruction outputs. `generate_api_recon` is the testable core
(providers + env injected); `main()` wires the real reference image, PROVIDERS, and the
recon scorer. Commits per object. API keys come from env and are never logged.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app import ingest  # noqa: E402
from app.models import Task  # noqa: E402

TOMATO_TITLE = "Solanum lycopersicum — single-image → 3D reconstruction"


def generate_api_recon(
    db, image_bytes, *, providers, env, score_fn=None, task_title=TOMATO_TITLE
) -> dict:
    task = db.execute(select(Task).where(Task.title == task_title)).scalars().first()
    if task is None:
        raise RuntimeError(f"subject task not found: {task_title!r}")
    report = {"generated": 0, "skipped_no_key": 0, "errors": 0, "by_provider": {}}
    for slug, (fn, env_var, name) in providers.items():
        key = env.get(env_var)
        if not key:
            report["skipped_no_key"] += 1
            continue
        try:
            glb = fn(image_bytes, api_key=key)
            out, _created = ingest.register_output(
                db,
                task_id=task.id,
                generator_slug=slug,
                generator_name=name,
                data=glb,
                ext="glb",
                title=f"{name} recon",
                meta={"depiction": "whole_plant", "provider": slug, "from_reference": "tomato_ref"},
            )
            out.source = f"api:{slug}"
            out.license = f"{name} generated-asset terms"
            out.attribution = f"Generated by {name} from CC reference photo"
            out.external_url = "https://platform.tripo3d.ai/" if slug == "tripo" else ""
            db.commit()  # provenance committed → hosted
            report["generated"] += 1
            report["by_provider"][slug] = report["by_provider"].get(slug, 0) + 1
            if score_fn is not None:
                try:
                    score_fn(db, out)
                    db.commit()
                except Exception as e:  # noqa: BLE001 — scoring best-effort; object stays hosted
                    print(f"  score failed for {out.id}: {e}")
                    db.rollback()
        except Exception as e:  # noqa: BLE001 — one provider never aborts the batch
            # Tripo passes the key in a header, never in exception text, so str(e) is safe.
            print(f"  {slug} generation failed: {type(e).__name__}: {e}")
            report["errors"] += 1
            db.rollback()
    return report


def main() -> int:
    import os

    from app import recon_service
    from app.database import SessionLocal
    from app.image3d import PROVIDERS

    ref = Path(__file__).resolve().parent.parent / "data/assets/reference/tomato_ref.jpg"
    if not ref.exists():
        print(f"reference image missing: {ref} — source the CC photo first")
        return 1
    image_bytes = ref.read_bytes()
    active = {s: v for s, v in PROVIDERS.items() if os.environ.get(v[1])}
    if not active:
        print("no provider API key in env (e.g. TRIPO_API_KEY) — nothing to generate")
        return 0
    db = SessionLocal()
    try:
        report = generate_api_recon(
            db, image_bytes, providers=active, env=os.environ,
            score_fn=recon_service.score_and_store,
        )
        print(report)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_generate_api_recon.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add scripts/generate_api_recon.py tests/test_generate_api_recon.py
git commit -m "feat(api-gen): generation adapter — ingest API recons as AI reconstructions"
```

---

### Task 4: Canonical reference image — serving + spotlight panel

> The actual CC photo is sourced + license-vetted + placed at
> `data/assets/reference/tomato_ref.jpg` by the controller (an operational step, with
> `data/assets/reference/tomato_ref.json` recording source_url/license/attribution). This
> task wires the spotlight to SHOW it; the test does not need the photo bytes (Jinja renders
> the resolved URL regardless).

**Files:**

- Modify: `app/spotlight.py` (`SPOTLIGHTS` tomato entry + `build_spotlight` reference_image resolution)
- Test: `tests/test_spotlight_page.py`

**Interfaces:**

- Consumes: `app.storage.get_storage().url_for(path)` (already used for thumbnails).
- Produces: `build_spotlight(...)["reference_image"]` is a served URL when the spotlight sets a relative asset path, else None.

- [ ] **Step 1: Write the failing test** (append to `tests/test_spotlight_page.py`)

```python
def test_reference_image_resolved_to_served_url(monkeypatch):
    from app.main import app
    from app.storage import get_storage

    db = SessionLocal()
    try:
        _seed_subject(db)  # existing helper in this module
    finally:
        db.close()
    monkeypatch.setattr(
        spotlight,
        "SPOTLIGHTS",
        [
            {
                "slug": "test",
                "task_title": "Spotlight Test Subject",
                "featured": True,
                "order": 0,
                "blurb": "b",
                "reference_image": "reference/tomato_ref.jpg",
            },
        ],
    )
    page = TestClient(app).get("/spotlight/test")
    assert page.status_code == 200
    # url_for prefixes with the static mount (verified: LocalStorageBackend ->
    # "/assets/reference/tomato_ref.jpg"). The RED state renders the RAW relative path
    # ("reference/tomato_ref.jpg"); GREEN renders the resolved served URL.
    expected = get_storage().url_for("reference/tomato_ref.jpg")
    assert 'class="ref-img"' in page.text  # the reference <img> rendered
    assert f'src="{expected}"' in page.text  # resolved served URL, not the raw relative path
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest "tests/test_spotlight_page.py::test_reference_image_resolved_to_served_url" -v`
Expected: FAIL — `build_spotlight` returns `reference_image` verbatim, so the page renders
`src="reference/tomato_ref.jpg"`, not the resolved `src="/assets/reference/tomato_ref.jpg"`;
the `src="{expected}"` assertion fails.

- [ ] **Step 3: Implement** — in `app/spotlight.py`:

(a) In `build_spotlight`, resolve the reference image through storage so it is a fetchable URL. Find the return dict's `"reference_image": spot["reference_image"]` and change it to:

```python
        "reference_image": (
            storage.url_for(spot["reference_image"]) if spot["reference_image"] else None
        ),
```

(`storage` is already bound earlier in `build_spotlight` via `get_storage()`.)

(b) Set the tomato spotlight's reference image. In `SPOTLIGHTS`, change the `tomato` entry's `"reference_image": None` to:

```python
        "reference_image": "reference/tomato_ref.jpg",
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_spotlight_page.py -v`
Expected: PASS (all spotlight page tests). Adjust the test's URL assertion to match `get_storage().url_for("reference/tomato_ref.jpg")` if the storage URL has a prefix.

- [ ] **Step 5: Run the full suite + commit**

```bash
.venv/bin/python -m pytest -q   # expect: all pass
git add app/spotlight.py tests/test_spotlight_page.py
git commit -m "feat(spotlight): show the canonical reference photo in the tomato panel"
```

---

## Out of scope (do NOT build here)

- The LIVE Tripo generation run — key-gated and operational. When `TRIPO_API_KEY` is set, run
  `.venv/bin/python scripts/generate_api_recon.py`, then render thumbnails and run the
  independent-critic gate (same as Plant3D). Not a build task.
- Additional providers (Meshy/Rodin/Hunyuan) — each a `PROVIDERS` entry + one `generate_*` fn.
- Infinigen procedural generation — a separate increment.

## Notes for the implementer

- Verify Tripo's exact endpoint paths, request bodies, the upload token field name
  (`image_token` vs `file_token`), the status strings, and the result model-URL field against
  https://platform.tripo3d.ai/docs (use context7 `query-docs` or WebFetch the docs page).
  The unit tests pin the ORCHESTRATION via the fake transport; the real `TripoTransport` is the
  live binding, so its field names only matter for the key-gated run — but get them right.
- NEVER print, echo, or log an API key. Keys live in the `Authorization` header only.
- If `tests/test_source_class.py` does not exist yet, create it with the standard import header.
