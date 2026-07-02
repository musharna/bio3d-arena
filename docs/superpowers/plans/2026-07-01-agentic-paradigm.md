# Agentic 3D Paradigm Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `agentic` 3D paradigm — an LLM that iteratively refines a Blender-authored plant mesh via a render→critique→revise loop — reusing the commission harness, so the arena can compare agentic iteration vs one-shot procedural_llm (same models).

**Architecture:** New `app/agentic.py` holds a self-contained headless-Blender GLB→PNG renderer, an OpenRouter vision call, and the loop (`agentic_generate`). It reuses `app/commission.py` (`build_prompt`, `extract_script`, `run_bpy`, `is_valid_mesh`). Outputs are `ModelOutput(source="agentic:<model>")` under a distinct `agentic-<model>` generator; `classify()` routes `agentic:` → `agentic`. A thin `scripts/generate_agentic.py` wires the real functions, roster, and taxa.

**Tech Stack:** Python, SQLAlchemy, Blender 4.2 (`blender --background --python`), OpenRouter (OpenAI-compatible, vision), trimesh (mesh validation), pytest.

## Global Constraints

- Reuse `app/commission.py` — do NOT reimplement `build_prompt`, `extract_script`, `run_bpy`, `is_valid_mesh`.
- Output source tag is exactly `f"agentic:{model_id}"`; generator slug is `"agentic-" + re.sub(r"[^a-z0-9]+","-",model_id.lower()).strip("-")` (DISTINCT from commission's `openrouter-` slug so paradigms don't collide).
- `classify()` must route `agentic:` → `agentic`; existing `api:` / `api:text:` rules stay unchanged.
- **No new DB table** — iteration metadata (`model_id`, `modality:"agentic"`, `n_iterations`, `iter_vertices`) goes in `ModelOutput.meta_json`.
- **Fallback / no-regression:** a failed or invalid revise never replaces a previously-valid mesh; the output is always the best valid mesh seen.
- **Idempotent:** skip a `(task_id, agentic-generator)` that already has an output (safe re-run).
- Default `n_iters=2` (iter-0 generate + 1 revise); configurable via `--iters`.
- Vision call `content` MUST be the OpenAI list form: `[{"type":"text","text":...},{"type":"image_url","image_url":{"url":"data:image/png;base64,<b64>"}}]`. API key in the `Authorization` header only, never in prompt/log/exception text.
- Roster (script default): `anthropic/claude-opus-4.8`, `google/gemini-3.1-pro-preview`, `openai/gpt-5.1`.
- `run_bpy` returns a dict with keys: `status` (`"ok"|"invalid_mesh"|"timeout"|"error"`), `stderr`, `duration_ms`, `glb_path` (str|None), `mesh_stats` (`{"meshes","vertices","faces"}`). `is_valid_mesh(path) -> (bool, stats)`. `run_fn(script, out_glb)` is the injected wrapper `lambda s, g: commission.run_bpy(s, out_glb=g)`.

---

### Task 1: Headless Blender GLB→PNG renderer (`render_glb_png`)

**Files:**

- Create: `app/agentic.py`
- Test: `tests/test_agentic_render.py`

**Interfaces:**

- Produces: `render_glb_png(glb_path, *, blender_bin="blender", timeout_s=120) -> bytes` (PNG bytes; raises `RuntimeError` on failure). Module constant `RENDER_SCRIPT: str`.

- [ ] **Step 1: Write the failing test** (real-execution smoke; skips if Blender absent)

```python
# tests/test_agentic_render.py
import shutil
import pytest
import trimesh
from app.agentic import render_glb_png


@pytest.mark.skipif(shutil.which("blender") is None, reason="Blender not installed")
def test_render_glb_png_returns_nonempty_png(tmp_path):
    glb = tmp_path / "box.glb"
    glb.write_bytes(trimesh.creation.box().export(file_type="glb"))
    png = render_glb_png(str(glb))
    assert isinstance(png, bytes) and len(png) > 1000
    assert png[:8] == b"\x89PNG\r\n\x1a\n"  # PNG magic
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_agentic_render.py -v`
Expected: FAIL — `ModuleNotFoundError` / `ImportError: cannot import name 'render_glb_png'` (or SKIP if no Blender — then verify via Step 4's manual run).

- [ ] **Step 3: Write minimal implementation**

```python
# app/agentic.py
"""Agentic 3D paradigm: an LLM iteratively refines a Blender-authored plant mesh via visual
feedback (render -> critique -> revise), reusing the commission harness. Distinct from
procedural_llm (one-shot). Outputs are ModelOutput(source="agentic:<model>")."""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from .commission import build_prompt, extract_script  # reused; run_bpy used via injected run_fn

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
_SECRET_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD")

# Trusted (our own) headless render: import the GLB at IN_GLB, frame all mesh geometry with a
# 3/4 camera + sun over a dark world, render a 512² PNG to OUT_PNG. Uses Blender's default engine.
RENDER_SCRIPT = r'''
import bpy, os, math, mathutils
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=os.environ["IN_GLB"])
objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
if not objs:
    raise SystemExit("no mesh in glb")
mn = mathutils.Vector((1e18, 1e18, 1e18)); mx = mathutils.Vector((-1e18, -1e18, -1e18))
for o in objs:
    for c in o.bound_box:
        w = o.matrix_world @ mathutils.Vector(c)
        mn = mathutils.Vector((min(mn[i], w[i]) for i in range(3)))
        mx = mathutils.Vector((max(mx[i], w[i]) for i in range(3)))
center = (mn + mx) / 2
radius = max((mx - mn)) / 2 or 1.0
cam_d = bpy.data.cameras.new("c"); cam = bpy.data.objects.new("c", cam_d)
bpy.context.scene.collection.objects.link(cam)
d = radius * 3.0
cam.location = center + mathutils.Vector((d * 0.8, -d * 0.8, d * 0.6))
cam.rotation_euler = (center - cam.location).to_track_quat("-Z", "Y").to_euler()
bpy.context.scene.camera = cam
sd = bpy.data.lights.new("s", type="SUN"); sd.energy = 3.0
s = bpy.data.objects.new("s", sd); bpy.context.scene.collection.objects.link(s)
s.rotation_euler = (math.radians(50), 0, math.radians(30))
scn = bpy.context.scene
scn.render.resolution_x = 512; scn.render.resolution_y = 512
scn.render.image_settings.file_format = "PNG"
scn.render.filepath = os.environ["OUT_PNG"]
bpy.ops.render.render(write_still=True)
'''


def _render_env(in_glb: str, out_png: str) -> dict:
    env = {k: v for k, v in os.environ.items() if not any(m in k.upper() for m in _SECRET_MARKERS)}
    env["IN_GLB"] = in_glb
    env["OUT_PNG"] = out_png
    return env


def render_glb_png(glb_path, *, blender_bin: str = "blender", timeout_s: int = 120) -> bytes:
    """Headless-Blender render of a GLB to PNG bytes (3/4 view, 512²). Raises RuntimeError on
    any failure (non-zero exit, missing/empty output)."""
    glb_path = str(glb_path)
    with tempfile.TemporaryDirectory() as td:
        script = Path(td) / "render.py"
        script.write_text(RENDER_SCRIPT)
        out_png = Path(td) / "out.png"
        cmd = [blender_bin, "--background", "--python", str(script)]
        proc = subprocess.run(
            cmd,
            env=_render_env(glb_path, str(out_png)),
            cwd=td,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        if proc.returncode != 0 or not out_png.exists() or out_png.stat().st_size == 0:
            raise RuntimeError(f"render failed: rc={proc.returncode} {proc.stderr[-500:]}")
        return out_png.read_bytes()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_agentic_render.py -v`
Expected: PASS (or SKIP if no Blender — if skipped, run once manually where Blender exists and confirm a real box GLB yields a non-empty PNG).

- [ ] **Step 5: Commit**

```bash
git add app/agentic.py tests/test_agentic_render.py
git commit -m "feat(agentic): headless Blender GLB->PNG renderer for the critique loop"
```

---

### Task 2: OpenRouter vision completion (`vision_complete`)

**Files:**

- Modify: `app/agentic.py`
- Test: `tests/test_agentic_vision.py`

**Interfaces:**

- Consumes: `OPENROUTER_URL` (module constant from Task 1).
- Produces: `vision_complete(post, model_id, prompt, image_png, *, api_key, max_tokens=32000, max_retries=3, sleep_fn=None) -> str`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_agentic_vision.py
from app.agentic import vision_complete


class _Resp:
    def __init__(self, text):
        self._text = text

    def raise_for_status(self):
        pass

    def json(self):
        return {"choices": [{"message": {"content": self._text}}]}


def test_vision_complete_builds_vision_content_and_returns_text():
    captured = {}

    def fake_post(url, *, headers, json, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return _Resp("IMPROVED SCRIPT")

    out = vision_complete(
        fake_post, "openai/gpt-5.1", "critique this", b"\x89PNGdata", api_key="k"
    )
    assert out == "IMPROVED SCRIPT"
    content = captured["json"]["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "critique this"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert captured["headers"]["Authorization"] == "Bearer k"  # key in header only
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_agentic_vision.py -v`
Expected: FAIL — `ImportError: cannot import name 'vision_complete'`.

- [ ] **Step 3: Write minimal implementation** (append to `app/agentic.py`)

```python
def vision_complete(
    post,
    model_id: str,
    prompt: str,
    image_png: bytes,
    *,
    api_key: str,
    max_tokens: int = 32000,
    max_retries: int = 3,
    sleep_fn=None,
) -> str:
    """One OpenRouter vision completion (text + one PNG). `post` injected (httpx.post) for tests.
    Same bounded-retry shape as commission.openrouter_complete. Key goes in the header only."""
    sleep = sleep_fn or time.sleep
    b64 = base64.b64encode(image_png).decode()
    content = [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
    ]
    last_exc = None
    for attempt in range(max_retries):
        try:
            resp = post(
                OPENROUTER_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model_id,
                    "messages": [{"role": "user", "content": content}],
                    "max_tokens": max_tokens,
                },
                timeout=180,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except Exception as e:  # noqa: BLE001 — bounded retry on transient dispatch failures
            last_exc = e
            if attempt < max_retries - 1:
                sleep(2**attempt)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("vision_complete: max_retries must be >= 1")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_agentic_vision.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/agentic.py tests/test_agentic_vision.py
git commit -m "feat(agentic): OpenRouter vision completion for self-critique"
```

---

### Task 3: The agentic loop (`agentic_generate` + generator + ingest + idempotency)

**Files:**

- Modify: `app/agentic.py`
- Test: `tests/test_agentic_loop.py`

**Interfaces:**

- Consumes: `build_prompt`, `extract_script` (from commission, imported in Task 1); `render_glb_png` (Task 1); `vision_complete` (Task 2).
- Produces: `agentic_slug(model_id) -> str`; `get_or_create_agentic_generator(db, model_id) -> Generator`; `critique_prompt(species, common) -> str`; `agentic_generate(db, *, model_id, task_id, species, common, complete_fn, vision_fn, run_fn, render_fn, asset_dir, n_iters=2) -> dict`. Report dict has `status` (`"ok"|"skipped_exists"|"error"|"invalid_mesh"|"timeout"`), `model_id`, `task_id`, and on ok `n_iterations`, `output_id`.

- [ ] **Step 1: Write the failing tests**

````python
# tests/test_agentic_loop.py
import json

import trimesh
from sqlalchemy import select

from app import agentic
from app.database import SessionLocal, init_db
from app.models import Category, ModelOutput, Task

TITLE = "Zea mays — single-image → 3D reconstruction"


def setup_module(_m):
    init_db()


def _task(db):
    cat = db.query(Category).filter_by(slug="plants").first() or Category(slug="plants", name="P")
    db.add(cat)
    db.flush()
    t = Task(category_id=cat.id, title=TITLE, prompt="p")
    db.add(t)
    db.commit()
    return t


def _ok_run(vertices):
    """A run_fn that writes a real box GLB and reports `vertices`."""

    def run_fn(script, out_glb):
        from pathlib import Path

        Path(out_glb).write_bytes(trimesh.creation.box().export(file_type="glb"))
        return {
            "status": "ok",
            "glb_path": str(out_glb),
            "mesh_stats": {"vertices": vertices, "faces": 12, "meshes": 1},
            "duration_ms": 1,
        }

    return run_fn


def _bad_run(script, out_glb):
    return {"status": "invalid_mesh", "glb_path": None, "mesh_stats": {}, "duration_ms": 1}


def test_agentic_adopts_valid_revision(tmp_path):
    db = SessionLocal()
    try:
        t = _task(db)
        calls = {"run": 0}

        def run_fn(script, out_glb):
            calls["run"] += 1
            return _ok_run(8 if calls["run"] == 1 else 99)(script, out_glb)

        rep = agentic.agentic_generate(
            db,
            model_id="openai/gpt-5.1",
            task_id=t.id,
            species="Zea mays",
            common="maize",
            complete_fn=lambda prompt: "```python\npass\n```",
            vision_fn=lambda prompt, png: "```python\npass\n```",
            run_fn=run_fn,
            render_fn=lambda glb: b"\x89PNGfake",
            asset_dir=str(tmp_path),
            n_iters=2,
        )
        assert rep["status"] == "ok" and rep["n_iterations"] == 2
        out = db.execute(select(ModelOutput).where(ModelOutput.id == rep["output_id"])).scalar_one()
        assert out.source == "agentic:openai/gpt-5.1"
        meta = json.loads(out.meta_json)
        assert meta["modality"] == "agentic" and meta["n_iterations"] == 2
        assert meta["iter_vertices"] == [8, 99]
        assert out.generator.paradigm != "procedural_llm"  # distinct generator
    finally:
        db.close()


def test_agentic_keeps_iter0_when_revision_invalid(tmp_path):
    db = SessionLocal()
    try:
        t = _task(db)
        calls = {"run": 0}

        def run_fn(script, out_glb):
            calls["run"] += 1
            return _ok_run(8)(script, out_glb) if calls["run"] == 1 else _bad_run(script, out_glb)

        rep = agentic.agentic_generate(
            db,
            model_id="x/m",
            task_id=t.id,
            species="Zea mays",
            common="maize",
            complete_fn=lambda prompt: "s",
            vision_fn=lambda prompt, png: "s",
            run_fn=run_fn,
            render_fn=lambda glb: b"png",
            asset_dir=str(tmp_path),
            n_iters=2,
        )
        assert rep["status"] == "ok" and rep["n_iterations"] == 1  # kept iter-0, no regression
    finally:
        db.close()


def test_agentic_no_output_when_iter0_invalid(tmp_path):
    db = SessionLocal()
    try:
        t = _task(db)
        rep = agentic.agentic_generate(
            db,
            model_id="x/m2",
            task_id=t.id,
            species="Zea mays",
            common="maize",
            complete_fn=lambda prompt: "s",
            vision_fn=lambda prompt, png: "s",
            run_fn=_bad_run,
            render_fn=lambda glb: b"png",
            asset_dir=str(tmp_path),
            n_iters=2,
        )
        assert rep["status"] == "invalid_mesh"
        gen = agentic.get_or_create_agentic_generator(db, "x/m2")
        assert db.query(ModelOutput).filter_by(generator_id=gen.id).count() == 0
    finally:
        db.close()


def test_agentic_idempotent(tmp_path):
    db = SessionLocal()
    try:
        t = _task(db)
        kw = dict(
            task_id=t.id,
            species="Zea mays",
            common="maize",
            complete_fn=lambda prompt: "s",
            vision_fn=lambda prompt, png: "s",
            run_fn=_ok_run(8),
            render_fn=lambda glb: b"png",
            asset_dir=str(tmp_path),
            n_iters=1,
        )
        r1 = agentic.agentic_generate(db, model_id="x/m3", **kw)
        r2 = agentic.agentic_generate(db, model_id="x/m3", **kw)
        assert r1["status"] == "ok" and r2["status"] == "skipped_exists"
        gen = agentic.get_or_create_agentic_generator(db, "x/m3")
        assert db.query(ModelOutput).filter_by(generator_id=gen.id).count() == 1
    finally:
        db.close()
````

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_agentic_loop.py -v`
Expected: FAIL — `AttributeError: module 'app.agentic' has no attribute 'agentic_generate'`.

- [ ] **Step 3: Write minimal implementation** (append to `app/agentic.py`)

```python
def agentic_slug(model_id: str) -> str:
    return "agentic-" + re.sub(r"[^a-z0-9]+", "-", model_id.lower()).strip("-")


def get_or_create_agentic_generator(db, model_id: str):
    from .models import Generator

    slug = agentic_slug(model_id)
    gen = db.query(Generator).filter_by(slug=slug).first()
    if gen is None:
        gen = Generator(
            slug=slug,
            name=f"{model_id} (agentic)",
            kind="model",
            description="agentic (iterative render-critique-revise) via OpenRouter",
        )
        db.add(gen)
        db.flush()
    return gen


def critique_prompt(species: str, common: str) -> str:
    return (
        f"The attached image is a render of YOUR current 3D mesh of a {common} plant "
        f"({species}), built by your previous Blender-Python script. Critically compare it to a "
        f"real {common}: name what is wrong or missing (proportions, missing organs, leaf/needle "
        "shape, topology, obvious artefacts). Then output ONLY an improved, COMPLETE Blender 4.2 "
        "bpy script that fixes those issues and re-exports GLB to os.environ['OUT_GLB'] — no "
        "explanation, no markdown."
    )


def agentic_generate(
    db,
    *,
    model_id: str,
    task_id: int,
    species: str,
    common: str,
    complete_fn,
    vision_fn,
    run_fn,
    render_fn,
    asset_dir,
    n_iters: int = 2,
) -> dict:
    """One agentic generation for (model, task): generate a bpy script, then up to n_iters-1
    render->critique->revise rounds. `complete_fn(prompt)->str`, `vision_fn(prompt, png)->str`,
    `run_fn(script, out_glb)->run-dict`, `render_fn(glb_path)->png bytes`. Idempotent per
    (task, agentic-generator). A failed/invalid revise never regresses below the last valid mesh."""
    from .models import ModelOutput

    gen = get_or_create_agentic_generator(db, model_id)
    if db.query(ModelOutput).filter_by(task_id=task_id, generator_id=gen.id).first() is not None:
        return {"status": "skipped_exists", "model_id": model_id, "task_id": task_id}

    with tempfile.TemporaryDirectory() as td:
        # iteration 0
        script = extract_script(complete_fn(build_prompt(species, common)))
        run = run_fn(script, str(Path(td) / "iter0.glb"))
        if run.get("status") != "ok" or not run.get("glb_path"):
            return {"status": run.get("status", "error"), "model_id": model_id, "task_id": task_id}
        best_path = run["glb_path"]
        iter_vertices = [run.get("mesh_stats", {}).get("vertices", 0)]

        # revise iterations
        for i in range(1, n_iters):
            try:
                png = render_fn(best_path)
            except Exception:  # noqa: BLE001 — render failure stops refinement, keeps best mesh
                break
            new_script = extract_script(vision_fn(critique_prompt(species, common), png))
            run2 = run_fn(new_script, str(Path(td) / f"iter{i}.glb"))
            if run2.get("status") == "ok" and run2.get("glb_path"):
                best_path = run2["glb_path"]
                iter_vertices.append(run2.get("mesh_stats", {}).get("vertices", 0))

        rel = Path("agentic") / f"{gen.slug}_{task_id}.glb"
        dst = Path(asset_dir) / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(best_path, dst)
        out = ModelOutput(
            task_id=task_id,
            generator_id=gen.id,
            title=f"{model_id} (agentic)",
            asset_path=str(rel),
            asset_format="glb",
            source=f"agentic:{model_id}",
            meta_json=json.dumps(
                {
                    "model_id": model_id,
                    "modality": "agentic",
                    "n_iterations": len(iter_vertices),
                    "iter_vertices": iter_vertices,
                }
            ),
        )
        db.add(out)
        db.commit()
        return {
            "status": "ok",
            "model_id": model_id,
            "task_id": task_id,
            "n_iterations": len(iter_vertices),
            "output_id": out.id,
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_agentic_loop.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add app/agentic.py tests/test_agentic_loop.py
git commit -m "feat(agentic): render-critique-revise loop with no-regression fallback + idempotency"
```

---

### Task 4: Classify `agentic:` → `agentic`

**Files:**

- Modify: `scripts/backfill_paradigms.py` (the `classify` function, add a rule beside the `api:text:` one)
- Test: `tests/test_backfill_paradigms.py` (add a test)

**Interfaces:**

- Consumes: `classify(slug, kind, sources)` (existing).

- [ ] **Step 1: Write the failing test** (append to `tests/test_backfill_paradigms.py`)

```python
def test_classify_agentic_source_is_agentic():
    assert classify("agentic-openai-gpt-5-1", "model", {"agentic:openai/gpt-5.1"}) == "agentic"
    # regression: image/text api sources unchanged
    assert classify("fal:trellis", "model", {"api:fal:trellis"}) == "image_recon"
    assert classify("fal:tripo-p1-text", "model", {"api:text:fal:tripo-p1-text"}) == "text_native"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_backfill_paradigms.py::test_classify_agentic_source_is_agentic -v`
Expected: FAIL — `agentic:` currently returns `None` (no rule matches).

- [ ] **Step 3: Write minimal implementation**

In `scripts/backfill_paradigms.py`, inside `classify`, add the rule immediately after the `api:text:` rule (before the generic `api:` rule):

```python
    if any_src_prefix("agentic:"):
        return "agentic"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_backfill_paradigms.py -v`
Expected: PASS (all classify tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/backfill_paradigms.py tests/test_backfill_paradigms.py
git commit -m "feat(agentic): classify agentic: sources as the agentic paradigm"
```

---

### Task 5: Generation script (`scripts/generate_agentic.py`)

**Files:**

- Create: `scripts/generate_agentic.py`
- Test: `tests/test_generate_agentic.py`

**Interfaces:**

- Consumes: `agentic.agentic_generate`, `commission.SPECIES_COMMON`, `commission.resolve_taxon_tasks`, `commission.run_bpy`, `agentic.render_glb_png`, `agentic.vision_complete`, `commission.openrouter_complete`.
- Produces: `run_agentic_batch(db, *, roster, taxon_tasks, complete_fn, vision_fn, run_fn, render_fn, asset_dir, n_iters=2, crop=None) -> dict` (testable core); `main() -> int`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_generate_agentic.py
import trimesh

from app.database import SessionLocal, init_db
from app.models import Category, ModelOutput, Task
from scripts.generate_agentic import run_agentic_batch

TITLE = "Zea mays — single-image → 3D reconstruction"


def setup_module(_m):
    init_db()


def _task(db):
    cat = db.query(Category).filter_by(slug="plants").first() or Category(slug="plants", name="P")
    db.add(cat)
    db.flush()
    t = Task(category_id=cat.id, title=TITLE, prompt="p")
    db.add(t)
    db.commit()
    return t


def _run_fn(script, out_glb):
    from pathlib import Path

    Path(out_glb).write_bytes(trimesh.creation.box().export(file_type="glb"))
    return {"status": "ok", "glb_path": str(out_glb), "mesh_stats": {"vertices": 8}, "duration_ms": 1}


def test_run_agentic_batch_generates_and_is_idempotent(tmp_path):
    db = SessionLocal()
    try:
        t = _task(db)
        kw = dict(
            roster=["x/m"],
            taxon_tasks=[("Zea mays", t.id)],
            complete_fn=lambda model_id, prompt: "s",
            vision_fn=lambda model_id, prompt, png: "s",
            run_fn=_run_fn,
            render_fn=lambda glb: b"png",
            asset_dir=str(tmp_path),
            n_iters=1,
        )
        r1 = run_agentic_batch(db, **kw)
        assert r1["ok"] == 1
        r2 = run_agentic_batch(db, **kw)  # idempotent second pass
        assert r2["skipped_exists"] == 1 and r2["ok"] == 0
        assert db.query(ModelOutput).filter(ModelOutput.source.like("agentic:%")).count() == 1
    finally:
        db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_generate_agentic.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.generate_agentic'`.

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/generate_agentic.py
"""Generate agentic 3D outputs (render->critique->revise) per (model, taxon) and ingest them.
`run_agentic_batch` is the testable core (fns injected); `main()` wires the real OpenRouter +
Blender + render + roster. Key-gated: needs OPENROUTER_API_KEY. Study data is runtime, not committed."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import agentic, commission  # noqa: E402

ROSTER = [
    "anthropic/claude-opus-4.8",
    "google/gemini-3.1-pro-preview",
    "openai/gpt-5.1",
]


def run_agentic_batch(
    db,
    *,
    roster,
    taxon_tasks,
    complete_fn,
    vision_fn,
    run_fn,
    render_fn,
    asset_dir,
    n_iters: int = 2,
    crop: str | None = None,
) -> dict:
    """For each (model, (species, task_id)): agentic_generate. complete_fn(model_id, prompt)->str;
    vision_fn(model_id, prompt, png)->str. Skips existing (idempotent via agentic_generate)."""
    counts = {"ok": 0, "skipped_exists": 0, "error": 0, "invalid_mesh": 0, "timeout": 0}
    for species, task_id in taxon_tasks:
        if crop and crop.lower() not in species.lower():
            continue
        common = commission.SPECIES_COMMON.get(species, species)
        for model_id in roster:
            rep = agentic.agentic_generate(
                db,
                model_id=model_id,
                task_id=task_id,
                species=species,
                common=common,
                complete_fn=lambda prompt, _m=model_id: complete_fn(_m, prompt),
                vision_fn=lambda prompt, png, _m=model_id: vision_fn(_m, prompt, png),
                run_fn=run_fn,
                render_fn=render_fn,
                asset_dir=asset_dir,
                n_iters=n_iters,
            )
            counts[rep["status"]] = counts.get(rep["status"], 0) + 1
            print(f"  {species} / {model_id}: {rep['status']}"
                  + (f" (iters={rep.get('n_iterations')})" if rep["status"] == "ok" else ""))
    return counts


def main() -> int:
    import os

    import httpx

    from app import config
    from app.database import SessionLocal

    ap = argparse.ArgumentParser(description="Generate agentic (render-critique-revise) 3D outputs.")
    ap.add_argument("--crop", default=None, help="substring of a species to run just one taxon")
    ap.add_argument("--iters", type=int, default=2, help="iterations per output (>=1)")
    args = ap.parse_args()

    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        print("no OPENROUTER_API_KEY in env — nothing to generate")
        return 0

    def complete_fn(model_id, prompt):
        return commission.openrouter_complete(httpx.post, model_id, prompt, api_key=key)

    def vision_fn(model_id, prompt, png):
        return agentic.vision_complete(httpx.post, model_id, prompt, png, api_key=key)

    def run_fn(script, out_glb):
        return commission.run_bpy(script, out_glb=out_glb)

    db = SessionLocal()
    try:
        taxon_tasks = commission.resolve_taxon_tasks(db)
        counts = run_agentic_batch(
            db,
            roster=ROSTER,
            taxon_tasks=taxon_tasks,
            complete_fn=complete_fn,
            vision_fn=vision_fn,
            run_fn=run_fn,
            render_fn=agentic.render_glb_png,
            asset_dir=str(config.ASSET_DIR),
            n_iters=args.iters,
            crop=args.crop,
        )
        print(counts)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_generate_agentic.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/generate_agentic.py tests/test_generate_agentic.py
git commit -m "feat(agentic): generation script (roster x taxa, idempotent batch, CLI)"
```

---

## Post-implementation (controller-run, NOT a task — needs live Blender + OpenRouter + spend)

After all tasks pass and the full suite is green, the controller (not a subagent) runs the live
generation against the study DB — this is a spend + real-Blender step:

1. Snapshot the study DB (`cp data/study/arena-study.db data/backups/arena-study-preagentic-<ts>.db`).
2. `BIO3D_DATABASE_URL=sqlite:///…/data/study/arena-study.db BIO3D_DATA_DIR=…/bio3d-arena-mvp/data OPENROUTER_API_KEY=… python scripts/generate_agentic.py` (start with `--crop Zea` as a real-execution probe).
3. `backfill_paradigms.py --commit` → tags the new `agentic-*` generators `agentic`.
4. `render_thumbnails.py` → thumbnails for the new outputs.
5. Verify: agentic generators/outputs present, vote-pool probe shows `agentic` served, eyeball a couple renders. Checkpoint + snapshot the study DB.

```

```
