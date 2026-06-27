# Multi-View Recon Track (#21) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a multi-view recon track: single reference photo → NVS (novel-view synthesis, N views) → the existing multi-view→mesh recon providers → `recon:*` outputs, for pine + arabidopsis.

**Architecture:** New code is the **NVS view-generation step** (`generate_nvs` in `app/image3d.py`) + a per-subject orchestration script. The multi-view recon (`MULTIVIEW_PROVIDERS`, `generate_api_multiview`), the `recon:` source class, and ingest already exist and are reused. External-API contracts (NVS output format; NVS↔recon pose compatibility) are resolved by key-gated live checks; deterministic logic (de-tiling, orchestration) is TDD'd with fakes.

**Tech Stack:** Python, httpx, Pillow, pytest. Replicate NVS (candidate `jd7h/zero123plusplus`), fal multi-view recon. No new deps (Pillow already used).

## Global Constraints

- API keys (`REPLICATE_API_TOKEN`, `FAL_KEY`) from env, **never logged or put in exception text**.
- Reuse `ingest.register_output` (content-hash dedup) + `source="recon:*"`; no new DB tables; human vote/ranking path untouched.
- Skip-and-log honesty: NVS error or <2 views → skip subject, log, continue; one provider error never aborts the batch.
- Provider functions are called `fn(list_of_view_bytes, api_key=...)` (the `MULTIVIEW_PROVIDERS` contract).
- Tests: `setup_module` calls `init_db()`; unique row prefixes on the shared persistent test DB.
- KNOWN ENV GOTCHA: a PostToolUse `ruff` hook strips imports added separately from their first use — add imports together with their usage. Bash emits harmless `zsh: no matches found: (x86)/NVIDIA` noise.

---

### Task 1: `generate_nvs` provider + de-tiling + NVS registry

**Files:**

- Modify: `app/image3d.py` (add `generate_nvs`, `_normalize_views`, `NvsReplicateTransport`, `NVS_PROVIDERS`)
- Test: `tests/test_nvs.py`

**Interfaces:**

- Consumes: existing `Image3DError`, `_SUCCESS`, `_FAILED`, `_image_data_uri`, `_send_with_retry` in `app/image3d.py`.
- Produces: `generate_nvs(image_bytes: bytes, *, api_key: str, model: str, n_views: int = 6, grid: tuple[int,int] = (3,2), transport=None, timeout_s: int = 300, poll_interval_s: int = 5) -> list[bytes]` (list of N view PNGs); `NVS_PROVIDERS: dict[str, tuple]` (slug → (callable, env_var, name)) where the callable is `fn(image_bytes, api_key=...) -> list[bytes]`.

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_nvs.py -q`
Expected: FAIL — `ImportError: cannot import name '_normalize_views'` / `generate_nvs`.

- [ ] **Step 3: Write minimal implementation**

Append to `app/image3d.py` (after `generate_replicate` / the transports; keep `import io`, `from PIL import Image` at the existing import block — `_downscale_image` already imports PIL, so reuse that import):

```python
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
    grid: tuple[int, int] = (3, 2),
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
    # candidate; CONFIRM the exact slug + output format at the key-gated live probe (Step 6)
    "zero123plusplus": (
        functools.partial(generate_nvs, model="jd7h/zero123plusplus"),
        "REPLICATE_API_TOKEN",
        "Zero123++ (Replicate)",
    ),
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_nvs.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add app/image3d.py tests/test_nvs.py
git commit -m "feat(recon): generate_nvs (single image -> N views) + de-tiling + NVS registry"
```

- [ ] **Step 6: Key-gated LIVE PROBE (real-execution check — characterizes the NVS contract)**

Run (only with `REPLICATE_API_TOKEN` set):

```bash
.venv/bin/python -c "
import os
from app.image3d import NVS_PROVIDERS
from pathlib import Path
fn, env, name = NVS_PROVIDERS['zero123plusplus']
img = Path('.claude/worktrees/bio3d-arena-mvp/data/assets/reference/arabidopsis_ref.jpg').read_bytes()
views = fn(img, api_key=os.environ['REPLICATE_API_TOKEN'])
print('views:', len(views))
for i,v in enumerate(views[:2]):
    Path(f'/tmp/nvs_{i}.png').write_bytes(v)
"
```

Expected: 6 views; inspect `/tmp/nvs_0.png` is a plausible novel view of the rosette. **If the model returns a different count/layout or the slug 404s**, update `grid=`/`model=` in `NVS_PROVIDERS` (or pick another deployed NVS model: SV3D / stable-zero123 class) and adjust `_normalize_views` grid; re-run Steps 4+6. This step resolves the spec's NVS-format open item; record the confirmed model + view count in the commit message of Task 2.

---

### Task 2: per-subject orchestration script

**Files:**

- Create: `scripts/generate_multiview_recon.py`
- Test: `tests/test_generate_multiview_recon.py`

**Interfaces:**

- Consumes: `app.image3d.NVS_PROVIDERS` + `generate_nvs` (Task 1); existing `app.image3d.MULTIVIEW_PROVIDERS`; `scripts.generate_api_multiview.generate_api_multiview(db, views, *, providers, env, score_fn=None, task_title=...)`.
- Produces: `SUBJECTS: dict[str, dict]` (slug → {"ref": <path-under-asset-store>, "task_title": str}); `run_subject(db, subject, *, env, nvs_fn, mv_providers, views_dir, score_fn=None) -> dict` returning `{"subject", "n_views", "recon": <generate_api_multiview report>}` or `{"subject", "skipped": <reason>}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_generate_multiview_recon.py
from __future__ import annotations

import json

import trimesh
from sqlalchemy import select

from app.database import SessionLocal, init_db
from app.models import Category, ModelOutput, Task

from scripts.generate_multiview_recon import run_subject


def setup_module(_m):
    init_db()


PINE = "Pinus sylvestris — single-image → 3D reconstruction"


def _seed(db):
    db.query(ModelOutput).filter(ModelOutput.source.like("recon:%mvt%")).delete(
        synchronize_session=False
    )
    db.query(Task).filter_by(title=PINE).delete(synchronize_session=False)
    cat = db.query(Category).filter_by(slug="plants").first() or Category(slug="plants", name="P")
    db.add(cat)
    db.flush()
    db.add(Task(category_id=cat.id, title=PINE, prompt="p"))
    db.commit()


def _box():
    return trimesh.creation.box().export(file_type="glb")


def test_run_subject_nvs_then_multiview(tmp_path):
    with SessionLocal() as db:
        _seed(db)
        calls = {}

        def fake_nvs(image_bytes, *, api_key):
            calls["nvs"] = True
            return [b"v%d" % i for i in range(6)]

        def fake_mv(views, *, api_key):
            calls["mv_n"] = len(views)
            return _box()

        mv = {"recon:trellis-mv-mvt": (fake_mv, "FAL_KEY", "TRELLIS mv")}
        subj = {"ref": "reference/arabidopsis_ref.jpg", "task_title": PINE}  # any existing ref file
        res = run_subject(
            db, subj, env={"REPLICATE_API_TOKEN": "r", "FAL_KEY": "f"},
            nvs_fn=fake_nvs, mv_providers=mv, views_dir=tmp_path,
        )
        assert calls["nvs"] and calls["mv_n"] == 6
        assert res["n_views"] == 6 and res["recon"]["generated"] == 1
        out = db.execute(
            select(ModelOutput).where(ModelOutput.source == "recon:trellis-mv-mvt")
        ).scalars().one()
        assert json.loads(out.meta_json)["modality"] == "multiview"
        # views were cached to disk
        assert len(list(tmp_path.glob("*.png"))) == 6


def test_run_subject_skips_when_nvs_too_few():
    with SessionLocal() as db:
        _seed(db)

        def fake_nvs(image_bytes, *, api_key):
            return [b"only-one"]

        subj = {"ref": "reference/arabidopsis_ref.jpg", "task_title": PINE}
        res = run_subject(
            db, subj, env={"REPLICATE_API_TOKEN": "r", "FAL_KEY": "f"},
            nvs_fn=fake_nvs, mv_providers={"recon:x": (lambda *a, **k: _box(), "FAL_KEY", "x")},
            views_dir=None,
        )
        assert "skipped" in res
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_generate_multiview_recon.py -q`
Expected: FAIL — `ModuleNotFoundError: scripts.generate_multiview_recon`.

- [ ] **Step 3: Write minimal implementation**

Create `scripts/generate_multiview_recon.py`:

```python
"""Multi-view recon track: per subject, reference photo → NVS (N views) → existing multi-view
recon → recon:* outputs. New piece is the NVS view-generation + per-subject wiring; the MV recon
core (generate_api_multiview) + recon: source class are reused.

Run (key-gated REPLICATE_API_TOKEN + FAL_KEY):
    .venv/bin/python scripts/generate_multiview_recon.py [--subject pinus|arabidopsis] [--refresh]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402
from app.image3d import MULTIVIEW_PROVIDERS, NVS_PROVIDERS  # noqa: E402
from scripts.generate_api_multiview import generate_api_multiview  # noqa: E402

# slug → reference photo (asset-store-relative) + recon Task title (the GT-bound subject task).
SUBJECTS: dict[str, dict] = {
    "pinus": {
        "ref": "reference/pinus_ref.jpg",
        "task_title": "Pinus sylvestris — single-image → 3D reconstruction",
    },
    "arabidopsis": {
        "ref": "reference/arabidopsis_ref.jpg",
        "task_title": "Arabidopsis thaliana — single-image → 3D reconstruction",
    },
}


def run_subject(db, subject, *, env, nvs_fn, mv_providers, views_dir, score_fn=None) -> dict:
    """nvs_fn(image_bytes, api_key=...) -> list[bytes]; mv_providers like MULTIVIEW_PROVIDERS."""
    ref = Path(config.ASSET_DIR) / subject["ref"]
    if not ref.exists():
        return {"subject": subject["task_title"], "skipped": f"missing ref {ref}"}
    rep_key = env.get("REPLICATE_API_TOKEN")
    if not rep_key:
        return {"subject": subject["task_title"], "skipped": "no REPLICATE_API_TOKEN"}
    try:
        views = nvs_fn(ref.read_bytes(), api_key=rep_key)
    except Exception as e:  # noqa: BLE001 — skip-and-log; provider passes key in header not text
        return {"subject": subject["task_title"], "skipped": f"nvs error: {type(e).__name__}: {e}"}
    if len(views) < 2:
        return {"subject": subject["task_title"], "skipped": f"nvs returned {len(views)} views"}
    if views_dir is not None:
        views_dir = Path(views_dir)
        views_dir.mkdir(parents=True, exist_ok=True)
        for i, v in enumerate(views):
            (views_dir / f"view_{i}.png").write_bytes(v)
    active = {s: v for s, v in mv_providers.items() if env.get(v[1])}
    report = generate_api_multiview(
        db, views, providers=active, env=env, score_fn=score_fn, task_title=subject["task_title"]
    )
    return {"subject": subject["task_title"], "n_views": len(views), "recon": report}


def main() -> int:
    import argparse
    import os

    from app import recon_service
    from app.database import SessionLocal

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--subject", choices=sorted(SUBJECTS), default=None)
    ap.add_argument("--refresh", action="store_true", help="ignore cached views (currently always regenerates)")
    args = ap.parse_args()
    subjects = [args.subject] if args.subject else list(SUBJECTS)
    nvs_fn = NVS_PROVIDERS["zero123plusplus"][0]
    with SessionLocal() as db:
        for slug in subjects:
            vdir = Path(config.ASSET_DIR) / "reference" / "views" / slug
            res = run_subject(
                db, SUBJECTS[slug], env=os.environ, nvs_fn=nvs_fn,
                mv_providers=MULTIVIEW_PROVIDERS, views_dir=vdir,
                score_fn=recon_service.score_and_store,
            )
            print(res)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_generate_multiview_recon.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/generate_multiview_recon.py tests/test_generate_multiview_recon.py
git commit -m "feat(recon): multi-view recon orchestration (subject -> NVS -> multi-view recon)"
```

---

### Task 3: full regression + key-gated end-to-end real-execution check

**Files:** none (verification only)

- [ ] **Step 1: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS — prior count + the new `test_nvs.py` (4) and `test_generate_multiview_recon.py` (2), 0 failed.

- [ ] **Step 2: Key-gated END-TO-END real-execution check (resolves the NVS↔recon pose-compat risk)**

Run (with `REPLICATE_API_TOKEN` + `FAL_KEY`, env `BIO3D_DATA_DIR` + `BIO3D_DB_PATH` pointing at the study setup):

```bash
.venv/bin/python scripts/generate_multiview_recon.py --subject arabidopsis
```

Then render one produced `recon:*` output (reuse `scripts/judge_capture.browser_capture_multi_factory`) and eyeball it.
Expected: at least one `recon:*` arabidopsis output is produced AND renders as a plausible rosette (not a blob). **If every `MULTIVIEW_PROVIDERS` entry errors or all outputs are blobs from the 6 NVS views** (pose mismatch / provider rejects the view set), that is the spec's flagged blocker — STOP and report which providers were tried + the failure, rather than shipping a broken track. Record the working provider(s) in the final notes.

- [ ] **Step 3: Confirm human path + single-image recon untouched**

Run: `git diff --stat main...HEAD -- app/ | grep -vE "image3d"` → expect no changes outside `app/image3d.py` (additions only). `scripts/generate_api_recon.py`, `app/service.py`, `app/main.py` unchanged.

---

## Self-Review

**Spec coverage:** NVS provider (T1) ✓; de-tiling for sheet vs list (T1) ✓; NVS registry + concrete candidate (T1) ✓; NVS-format live check (T1 Step 6) ✓; orchestration per-subject, parameterized (T2) ✓; pine+arabidopsis scope, view caching (T2) ✓; reuse generate_api_multiview + MULTIVIEW_PROVIDERS + recon: source (T2) ✓; NVS↔recon pose-compat live check + blocker-surfacing (T3 Step 2) ✓; honest skip-and-log (T2 run_subject) ✓; scoring deferred via score_fn (T2 main, AgriGen down) ✓; no new schema / human path untouched (T3 Step 3) ✓.

**Placeholder scan:** No TBD/TODO; complete code in every code step. The NVS model slug `jd7h/zero123plusplus` is a concrete candidate explicitly verified+pinned at T1 Step 6 (live), not a placeholder.

**Type consistency:** `generate_nvs(image_bytes, *, api_key, model, n_views=6, grid=(3,2), …) -> list[bytes]` and `_normalize_views(outs, n_views, grid)` consistent across T1 def/tests. `NVS_PROVIDERS[x][0]` is a partial callable `fn(image_bytes, api_key=...) -> list[bytes]`, consumed identically in T2. `run_subject(db, subject, *, env, nvs_fn, mv_providers, views_dir, score_fn=None)` signature matches its T2 test calls. `MULTIVIEW_PROVIDERS` entry shape `(fn, env_var, name)` with `fn(views, api_key=...)` matches `generate_api_multiview`'s existing contract.
