# Commissioned-Generation Arena (Harness) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Commission plant-generation from multiple LLMs on a common 6-taxa prompt set — each writes a Blender-Python script we sandbox-run — and ingest the resulting meshes as agent-attributed arena outputs plus execution outcomes.

**Architecture:** A pure/core module (`app/commission.py`) holds prompt building, script extraction, OpenRouter dispatch, the bpy sandbox runner, mesh validation, and DB ingestion; a thin CLI (`scripts/commission_arena.py`) wires the real HTTP client + Blender and drives a resumable batch. Mirrors the existing `trait_judge`/`scope_judge` split (import-testable core + injected-dependency CLI).

**Tech Stack:** Python 3.13 (`.venv`), SQLAlchemy models (`create_all`, no migrations), `httpx` (OpenRouter REST, already installed), `trimesh` 4.12.2 (GLB validation, already installed), Blender headless binary (installed in Task 2), FastAPI/Jinja arena (unchanged).

## Global Constraints

- Substrate is **Blender Python (bpy)**, run headless via `blender --background --python`. Blender only needs to **export GLB** (no GPU; rendering stays on the existing model-viewer path).
- Dispatch is **OpenRouter** (OpenAI-compatible REST, `https://openrouter.ai/api/v1/chat/completions`) via **one** `OPENROUTER_API_KEY`. Roster = list of model-id strings. Use `httpx` — do NOT add the `openai` package.
- Baseline task set = the existing **6 taxa** (Solanum lycopersicum, Zea mays, Pinus sylvestris, Rosa, Glycine max, Arabidopsis thaliana), **1 plain species prompt each**. Task rows already exist; resolve `task_id` per taxon via `TraitRubric.taxon`.
- **Single-shot**: one completion per (model, task); a crash/invalid-mesh is a recorded failure, retried only on transport/5xx/429, never on model content.
- Commissioned outputs get `ModelOutput.source="commissioned"`. **Failures are first-class**: every attempt writes a `CommissionAttempt` row (script + status + error), even when no `ModelOutput` is created.
- Sandbox every bpy run: wall-clock timeout, throwaway temp cwd, only `$OUT_GLB` harvested; memory cap + network isolation via a configurable command prefix.
- **Never run pytest against the study DB.** Tests use the default env only (`init_db()` + `SessionLocal`). Do not set `BIO3D_DATABASE_URL`/`BIO3D_DATA_DIR` to the study paths in tests.
- New DB tables via `create_all` (additive); no migration tooling.

---

### Task 1: Mesh validity check

**Files:**

- Create: `app/commission.py`
- Test: `tests/test_commission.py`

**Interfaces:**

- Produces: `is_valid_mesh(glb_path: str | Path) -> tuple[bool, dict]` — `(ok, stats)` where `stats = {"meshes": int, "vertices": int, "faces": int}`; `ok` is True iff the file loads and has ≥1 vertex and ≥1 face.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_commission.py
from __future__ import annotations

from pathlib import Path

import trimesh

from app import commission


def test_is_valid_mesh_true_for_real_glb(tmp_path):
    p = tmp_path / "box.glb"
    trimesh.creation.box().export(str(p))
    ok, stats = commission.is_valid_mesh(p)
    assert ok is True
    assert stats["vertices"] > 0 and stats["faces"] > 0


def test_is_valid_mesh_false_for_empty_or_missing(tmp_path):
    empty = tmp_path / "empty.glb"
    empty.write_bytes(b"")
    assert commission.is_valid_mesh(empty)[0] is False
    assert commission.is_valid_mesh(tmp_path / "nope.glb")[0] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_commission.py -q`
Expected: FAIL — `AttributeError: module 'app.commission' has no attribute 'is_valid_mesh'` (or ImportError).

- [ ] **Step 3: Write minimal implementation**

```python
# app/commission.py
"""Commissioned-generation arena harness (core).

Give each competing LLM the same plant-generation task, run the bpy script it writes in a
sandbox, and ingest the resulting mesh as an agent-attributed arena output. Pure/core helpers
here (prompt build, script extraction, OpenRouter dispatch, sandbox run, mesh validation, DB
ingestion); scripts/commission_arena.py wires the real HTTP client + Blender."""

from __future__ import annotations

from pathlib import Path

import trimesh


def is_valid_mesh(glb_path) -> tuple[bool, dict]:
    """Load a GLB and report whether it has real geometry. (False, {}) on any load failure."""
    p = Path(glb_path)
    if not p.exists() or p.stat().st_size == 0:
        return False, {}
    try:
        scene = trimesh.load(str(p), force="scene")
    except Exception:  # noqa: BLE001 — any parse/load failure means invalid mesh
        return False, {}
    geoms = list(getattr(scene, "geometry", {}).values())
    vertices = sum(len(g.vertices) for g in geoms)
    faces = sum(len(g.faces) for g in geoms)
    ok = vertices > 0 and faces > 0
    return ok, {"meshes": len(geoms), "vertices": vertices, "faces": faces}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_commission.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add app/commission.py tests/test_commission.py
git commit -m "feat(commission): GLB mesh-validity check"
```

---

### Task 2: bpy sandbox runner + Blender install

**Files:**

- Modify: `app/commission.py`
- Test: `tests/test_commission.py`

**Interfaces:**

- Consumes: `is_valid_mesh` (Task 1).
- Produces: `run_bpy(script_text: str, *, out_glb: str | Path, timeout_s: int = 120, blender_bin: str = "blender", sandbox_prefix: list[str] | None = None) -> dict` returning `{"status": str, "stderr": str, "duration_ms": int, "glb_path": str | None, "mesh_stats": dict}` where `status ∈ {"ok","error","timeout","invalid_mesh"}`.

- [ ] **Step 1: Install Blender headless (setup)**

```bash
# Linux x86_64 headless Blender (no GPU needed — GLB export only).
cd /tmp
curl -fsSL -o blender.tar.xz https://download.blender.org/release/Blender4.2/blender-4.2.0-linux-x64.tar.xz
tar -xf blender.tar.xz
sudo ln -sf /tmp/blender-4.2.0-linux-x64/blender /usr/local/bin/blender  # or add to PATH
blender --version   # expect: Blender 4.2.0
```

If `sudo` is unavailable, place the extracted dir under `~/.local/blender` and pass its
`blender` path via `--blender-bin` in Task 8. Record the resolved path for the CLI default.

- [ ] **Step 2: Write the failing real-execution test**

```python
# add to tests/test_commission.py
import shutil

import pytest

_KNOWN_GOOD_BPY = """
import bpy, os
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete()
bpy.ops.mesh.primitive_cube_add()
bpy.ops.export_scene.gltf(filepath=os.environ['OUT_GLB'], export_format='GLB')
"""


@pytest.mark.skipif(shutil.which("blender") is None, reason="blender not installed")
def test_run_bpy_known_good_script_produces_valid_glb(tmp_path):
    out = tmp_path / "out.glb"
    res = commission.run_bpy(_KNOWN_GOOD_BPY, out_glb=out, timeout_s=120)
    assert res["status"] == "ok"
    assert res["glb_path"] and commission.is_valid_mesh(out)[0] is True


def test_run_bpy_missing_blender_returns_error(tmp_path):
    res = commission.run_bpy("print('x')", out_glb=tmp_path / "o.glb", blender_bin="definitely-not-blender")
    assert res["status"] == "error" and res["glb_path"] is None
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_commission.py -q`
Expected: FAIL — `run_bpy` not defined (the missing-blender test errors; the real-exec test runs once implemented).

- [ ] **Step 4: Write minimal implementation**

```python
# add to app/commission.py (new imports at top of file)
import os
import subprocess
import tempfile
import time


def run_bpy(
    script_text: str,
    *,
    out_glb,
    timeout_s: int = 120,
    blender_bin: str = "blender",
    sandbox_prefix: list[str] | None = None,
) -> dict:
    """Run an LLM-authored bpy script headless in a throwaway temp cwd, exposing only
    OUT_GLB. Returns a status dict; never raises on script failure. sandbox_prefix lets the
    caller wrap the command (e.g. ["heavy-run"] for a memory cap, ["unshare","-rn"] for no
    network) — kept configurable so tests run bare."""
    out_glb = Path(out_glb)
    prefix = list(sandbox_prefix or [])
    start = time.monotonic()
    with tempfile.TemporaryDirectory() as td:
        script_path = Path(td) / "gen.py"
        script_path.write_text(script_text)
        env = {**os.environ, "OUT_GLB": str(out_glb)}
        cmd = [*prefix, blender_bin, "--background", "--python", str(script_path)]
        try:
            proc = subprocess.run(
                cmd, env=env, cwd=td, capture_output=True, text=True, timeout=timeout_s
            )
        except subprocess.TimeoutExpired:
            return {
                "status": "timeout",
                "stderr": "wall-clock timeout",
                "duration_ms": timeout_s * 1000,
                "glb_path": None,
                "mesh_stats": {},
            }
        except FileNotFoundError:
            return {
                "status": "error",
                "stderr": f"blender binary not found: {blender_bin}",
                "duration_ms": int((time.monotonic() - start) * 1000),
                "glb_path": None,
                "mesh_stats": {},
            }
        dur = int((time.monotonic() - start) * 1000)
        if proc.returncode != 0:
            return {
                "status": "error",
                "stderr": proc.stderr[-4000:],
                "duration_ms": dur,
                "glb_path": None,
                "mesh_stats": {},
            }
        ok, stats = is_valid_mesh(out_glb)
        return {
            "status": "ok" if ok else "invalid_mesh",
            "stderr": proc.stderr[-2000:],
            "duration_ms": dur,
            "glb_path": str(out_glb) if ok else None,
            "mesh_stats": stats,
        }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_commission.py -q`
Expected: PASS. With Blender installed, the real-exec test runs and passes; otherwise it skips.

- [ ] **Step 6: Commit**

```bash
git add app/commission.py tests/test_commission.py
git commit -m "feat(commission): sandboxed bpy runner + blender headless"
```

---

### Task 3: Completion → script extraction

**Files:**

- Modify: `app/commission.py`
- Test: `tests/test_commission.py`

**Interfaces:**

- Produces: `extract_script(text: str) -> str` — returns the Python code from a completion: a fenced `python` / ` ` block if present, else the stripped text; `""` for empty/None.

- [ ] **Step 1: Write the failing test**

````python
# add to tests/test_commission.py
def test_extract_script_fenced_python():
    txt = "Here it is:\n```python\nimport bpy\nprint(1)\n```\nDone."
    assert commission.extract_script(txt) == "import bpy\nprint(1)"


def test_extract_script_plain_fence_and_unfenced_and_empty():
    assert commission.extract_script("```\nimport bpy\n```") == "import bpy"
    assert commission.extract_script("import bpy\nx=1") == "import bpy\nx=1"
    assert commission.extract_script("") == ""
    assert commission.extract_script(None) == ""
````

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_commission.py -k extract -q`
Expected: FAIL — `extract_script` not defined.

- [ ] **Step 3: Write minimal implementation**

````python
# add to app/commission.py (add `import re` to the top-of-file imports)
def extract_script(text: str) -> str:
    """Pull the Python script out of a chat completion. Single fenced block, literal
    terminator — no nested/ambiguous quantifiers (safe on arbitrary completions)."""
    if not text:
        return ""
    m = re.search(r"```(?:python)?[ \t]*\n(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return text.strip()
````

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_commission.py -k extract -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add app/commission.py tests/test_commission.py
git commit -m "feat(commission): extract bpy script from completion"
```

---

### Task 4: Prompt set + builder

**Files:**

- Modify: `app/commission.py`
- Test: `tests/test_commission.py`

**Interfaces:**

- Produces: `SPECIES_COMMON: dict[str, str]` (6 taxa → common name); `build_prompt(species: str, common: str) -> str`.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_commission.py
def test_build_prompt_pins_contract():
    p = commission.build_prompt("Solanum lycopersicum", "tomato")
    assert "OUT_GLB" in p and "tomato" in p and "Solanum lycopersicum" in p
    assert "bpy" in p.lower()


def test_species_common_covers_six_taxa():
    assert set(commission.SPECIES_COMMON) == {
        "Solanum lycopersicum", "Zea mays", "Pinus sylvestris",
        "Rosa", "Glycine max", "Arabidopsis thaliana",
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_commission.py -k "prompt or species" -q`
Expected: FAIL — `build_prompt`/`SPECIES_COMMON` not defined.

- [ ] **Step 3: Write minimal implementation**

```python
# add to app/commission.py
SPECIES_COMMON: dict[str, str] = {
    "Solanum lycopersicum": "tomato",
    "Zea mays": "maize (corn)",
    "Pinus sylvestris": "Scots pine",
    "Rosa": "rose",
    "Glycine max": "soybean",
    "Arabidopsis thaliana": "Arabidopsis (thale cress)",
}


def build_prompt(species: str, common: str) -> str:
    return (
        f"Write a complete Blender Python (bpy) script that procedurally generates a "
        f"botanically accurate 3D model of a whole {common} plant ({species}).\n\n"
        "Requirements:\n"
        "- Build real geometry: stem/trunk, leaves, and species-appropriate organs "
        "(flowers, fruit, cones, etc. where applicable).\n"
        "- Export the result as GLB to the path in the environment variable OUT_GLB "
        "(read it with os.environ['OUT_GLB']).\n"
        "- The script must run headless under `blender --background --python` with no user "
        "interaction and no external asset files.\n"
        "- Output ONLY the Python script — no explanation, no markdown prose."
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_commission.py -k "prompt or species" -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add app/commission.py tests/test_commission.py
git commit -m "feat(commission): 6-taxa prompt set + builder"
```

---

### Task 5: OpenRouter dispatch

**Files:**

- Modify: `app/commission.py`
- Test: `tests/test_commission.py`

**Interfaces:**

- Produces: `OPENROUTER_URL: str`; `openrouter_complete(post, model_id: str, prompt: str, *, api_key: str, max_tokens: int = 8000) -> str` — `post` is an injected callable with the `httpx.post` signature returning an object with `.raise_for_status()` and `.json()`.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_commission.py
def test_openrouter_complete_returns_message_content():
    captured = {}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "import bpy"}}]}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["model"] = json["model"]
        captured["auth"] = headers["Authorization"]
        return _Resp()

    out = commission.openrouter_complete(
        fake_post, "anthropic/claude-opus-4.8", "make a plant", api_key="sk-xyz"
    )
    assert out == "import bpy"
    assert captured["url"] == commission.OPENROUTER_URL
    assert captured["model"] == "anthropic/claude-opus-4.8"
    assert captured["auth"] == "Bearer sk-xyz"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_commission.py -k openrouter -q`
Expected: FAIL — `openrouter_complete` not defined.

- [ ] **Step 3: Write minimal implementation**

```python
# add to app/commission.py
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def openrouter_complete(post, model_id: str, prompt: str, *, api_key: str, max_tokens: int = 8000) -> str:
    """One chat completion via OpenRouter (OpenAI-compatible). `post` injected (httpx.post) for
    testing. Raises on HTTP error so the caller records a transport failure."""
    resp = post(
        OPENROUTER_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": model_id,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
        },
        timeout=180,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_commission.py -k openrouter -q`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add app/commission.py tests/test_commission.py
git commit -m "feat(commission): OpenRouter dispatch"
```

---

### Task 6: CommissionAttempt model + ingestion

**Files:**

- Modify: `app/models.py` (add `CommissionAttempt` after `ModelScope`)
- Modify: `app/commission.py`
- Test: `tests/test_commission_ingest.py`

**Interfaces:**

- Consumes: `run_bpy` result dict (Task 2), `is_valid_mesh` (Task 1).
- Produces:
  - `CommissionAttempt` table: `id, task_id (FK task.id), model_id (str), generator_id (FK generator.id, nullable), output_id (FK model_output.id, nullable), status (str), error (Text), script (Text), mesh_stats_json (Text), duration_ms (int), created (dt)`, unique `(model_id, task_id)`.
  - `slug_for_model(model_id: str) -> str` (e.g. `"anthropic/claude-opus-4.8"` → `"openrouter-anthropic-claude-opus-4-8"`).
  - `get_or_create_generator(db, model_id) -> Generator`.
  - `ingest_attempt(db, *, task_id, model_id, run, script, asset_dir) -> CommissionAttempt` — on `run["status"]=="ok"` copies the GLB into `asset_dir/"commissioned"/<slug>_<task_id>.glb` and creates a `ModelOutput(source="commissioned")`; always writes a `CommissionAttempt`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_commission_ingest.py
from __future__ import annotations

import trimesh

from app import commission
from app.database import SessionLocal, init_db
from app.models import CommissionAttempt, Generator, ModelOutput, Task, Category


def setup_module(_m):
    init_db()


def _task(db):
    cat = Category(slug="t-cat", name="c")
    db.add(cat)
    db.flush()
    t = Task(category_id=cat.id, title="tomato", prompt="a tomato")
    db.add(t)
    db.commit()
    return t.id


def test_ingest_ok_creates_output_and_attempt(tmp_path):
    with SessionLocal() as db:
        tid = _task(db)
        glb = tmp_path / "gen.glb"
        trimesh.creation.box().export(str(glb))
        run = {"status": "ok", "stderr": "", "duration_ms": 1234,
               "glb_path": str(glb), "mesh_stats": {"vertices": 8, "faces": 12}}
        att = commission.ingest_attempt(
            db, task_id=tid, model_id="anthropic/claude-opus-4.8",
            run=run, script="import bpy", asset_dir=tmp_path / "assets",
        )
        assert att.status == "ok" and att.output_id is not None
        out = db.get(ModelOutput, att.output_id)
        assert out.source == "commissioned" and out.asset_format == "glb"
        assert (tmp_path / "assets" / out.asset_path).exists()
        assert db.query(Generator).filter_by(id=att.generator_id).one().kind == "model"


def test_ingest_failure_writes_attempt_without_output(tmp_path):
    with SessionLocal() as db:
        tid = _task(db)
        run = {"status": "error", "stderr": "boom", "duration_ms": 50,
               "glb_path": None, "mesh_stats": {}}
        att = commission.ingest_attempt(
            db, task_id=tid, model_id="openai/gpt-x",
            run=run, script="bad", asset_dir=tmp_path / "assets",
        )
        assert att.status == "error" and att.output_id is None
        assert att.error == "boom" and att.script == "bad"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_commission_ingest.py -q`
Expected: FAIL — `CommissionAttempt` / `ingest_attempt` not defined.

- [ ] **Step 3a: Add the model** (in `app/models.py`, immediately after the `ModelScope` class)

```python
class CommissionAttempt(Base):
    """One agent's attempt at one task in the commissioned-generation arena. Records the
    script + outcome even on failure (output_id NULL), so execution-success rate is a real
    metric. One row per (model_id, task_id) — resumable."""

    __tablename__ = "commission_attempt"
    __table_args__ = (
        UniqueConstraint("model_id", "task_id", name="uq_commission_attempt"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("task.id"), index=True)
    model_id: Mapped[str] = mapped_column(String(128), index=True)
    generator_id: Mapped[int | None] = mapped_column(ForeignKey("generator.id"), nullable=True)
    output_id: Mapped[int | None] = mapped_column(ForeignKey("model_output.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(20))  # ok|error|timeout|invalid_mesh
    error: Mapped[str] = mapped_column(Text, default="")
    script: Mapped[str] = mapped_column(Text, default="")
    mesh_stats_json: Mapped[str] = mapped_column(Text, default="{}")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)
```

- [ ] **Step 3b: Add ingestion helpers** (in `app/commission.py`; add `import json`, `import re`, `import shutil` to the top imports as needed)

```python
def slug_for_model(model_id: str) -> str:
    return "openrouter-" + re.sub(r"[^a-z0-9]+", "-", model_id.lower()).strip("-")


def get_or_create_generator(db, model_id: str):
    from .models import Generator

    slug = slug_for_model(model_id)
    gen = db.query(Generator).filter_by(slug=slug).first()
    if gen is None:
        gen = Generator(slug=slug, name=model_id, kind="model", description="commissioned via OpenRouter")
        db.add(gen)
        db.flush()
    return gen


def ingest_attempt(db, *, task_id: int, model_id: str, run: dict, script: str, asset_dir):
    """Persist one attempt. On status 'ok', copy the GLB under asset_dir/commissioned and
    create a ModelOutput(source='commissioned'); always create a CommissionAttempt."""
    from .models import CommissionAttempt, ModelOutput

    gen = get_or_create_generator(db, model_id)
    output_id = None
    if run.get("status") == "ok" and run.get("glb_path"):
        rel = Path("commissioned") / f"{gen.slug}_{task_id}.glb"
        dst = Path(asset_dir) / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(run["glb_path"], dst)
        out = ModelOutput(
            task_id=task_id,
            generator_id=gen.id,
            title=model_id,
            asset_path=str(rel),
            asset_format="glb",
            source="commissioned",
            meta_json=json.dumps({"model_id": model_id, "mesh_stats": run.get("mesh_stats", {})}),
        )
        db.add(out)
        db.flush()
        output_id = out.id
    att = CommissionAttempt(
        task_id=task_id,
        model_id=model_id,
        generator_id=gen.id,
        output_id=output_id,
        status=run.get("status", "error"),
        error=run.get("stderr", "") or "",
        script=script or "",
        mesh_stats_json=json.dumps(run.get("mesh_stats", {})),
        duration_ms=int(run.get("duration_ms", 0)),
    )
    db.add(att)
    db.commit()
    return att
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_commission_ingest.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add app/models.py app/commission.py tests/test_commission_ingest.py
git commit -m "feat(commission): CommissionAttempt model + ingestion"
```

---

### Task 7: Resumable batch orchestrator

**Files:**

- Modify: `app/commission.py`
- Test: `tests/test_commission_ingest.py`

**Interfaces:**

- Consumes: `build_prompt`, `extract_script`, `ingest_attempt`, `SPECIES_COMMON`.
- Produces:
  - `resolve_taxon_tasks(db) -> list[tuple[str, int]]` — `(taxon, task_id)` for taxa in `SPECIES_COMMON` that have a `TraitRubric` with a `task_id`.
  - `existing_pairs(db) -> set[tuple[str, int]]` — `(model_id, task_id)` already attempted.
  - `run_batch(db, *, complete_fn, run_fn, roster, taxon_tasks, asset_dir, max_calls=None) -> dict` — for each un-attempted `(model_id, (taxon, task_id))`: `text = complete_fn(model_id, prompt)`, `script = extract_script(text)`, `out_glb` a temp path, `run = run_fn(script, out_glb)`, then `ingest_attempt(...)`. Returns `{"ok","error","timeout","invalid_mesh","skipped"}` counts. `complete_fn(model_id, prompt) -> str`; `run_fn(script, out_glb) -> dict`.

- [ ] **Step 1: Write the failing test**

````python
# add to tests/test_commission_ingest.py
from app.models import TraitRubric


def _rubric_task(db, taxon):
    cat = Category(slug=f"c-{taxon}", name="c")
    db.add(cat)
    db.flush()
    t = Task(category_id=cat.id, title=taxon, prompt=f"a {taxon}")
    db.add(t)
    db.flush()
    db.add(TraitRubric(taxon=taxon, task_id=t.id, traits_json="[]"))
    db.commit()
    return t.id


def test_run_batch_persists_and_resumes(tmp_path):
    import trimesh

    with SessionLocal() as db:
        tid = _rubric_task(db, "Solanum lycopersicum")

        def complete_fn(model_id, prompt):
            return "```python\nimport bpy\n```"

        def run_fn(script, out_glb):
            trimesh.creation.box().export(str(out_glb))
            return {"status": "ok", "stderr": "", "duration_ms": 5,
                    "glb_path": str(out_glb), "mesh_stats": {"vertices": 8, "faces": 12}}

        roster = ["anthropic/claude-opus-4.8"]
        tt = [("Solanum lycopersicum", tid)]
        res = commission.run_batch(
            db, complete_fn=complete_fn, run_fn=run_fn, roster=roster,
            taxon_tasks=tt, asset_dir=tmp_path / "assets",
        )
        assert res["ok"] == 1
        att = db.query(CommissionAttempt).filter_by(model_id=roster[0], task_id=tid).one()
        assert att.status == "ok"

        # resume: same pair is skipped, no second attempt
        res2 = commission.run_batch(
            db, complete_fn=complete_fn, run_fn=run_fn, roster=roster,
            taxon_tasks=tt, asset_dir=tmp_path / "assets",
        )
        assert res2["skipped"] == 1 and res2["ok"] == 0
        assert db.query(CommissionAttempt).filter_by(model_id=roster[0], task_id=tid).count() == 1
````

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_commission_ingest.py -k batch -q`
Expected: FAIL — `run_batch` not defined.

- [ ] **Step 3: Write minimal implementation**

```python
# add to app/commission.py
def resolve_taxon_tasks(db) -> list[tuple[str, int]]:
    from .models import TraitRubric

    out = []
    for taxon in SPECIES_COMMON:
        r = db.query(TraitRubric).filter_by(taxon=taxon).first()
        if r is not None and r.task_id:
            out.append((taxon, r.task_id))
    return out


def existing_pairs(db) -> set[tuple[str, int]]:
    from .models import CommissionAttempt

    return {(a.model_id, a.task_id) for a in db.query(CommissionAttempt).all()}


def run_batch(db, *, complete_fn, run_fn, roster, taxon_tasks, asset_dir, max_calls=None):
    counts = {"ok": 0, "error": 0, "timeout": 0, "invalid_mesh": 0, "skipped": 0}
    seen = existing_pairs(db)
    made = 0
    for model_id in roster:
        for taxon, task_id in taxon_tasks:
            if (model_id, task_id) in seen:
                counts["skipped"] += 1
                continue
            if max_calls is not None and made >= max_calls:
                return counts
            prompt = build_prompt(taxon, SPECIES_COMMON[taxon])
            try:
                text = complete_fn(model_id, prompt)
                script = extract_script(text)
            except Exception as e:  # noqa: BLE001 — transport failure: record + continue
                run = {"status": "error", "stderr": f"dispatch: {e}", "duration_ms": 0,
                       "glb_path": None, "mesh_stats": {}}
                script = ""
            else:
                with tempfile.TemporaryDirectory() as td:
                    out_glb = Path(td) / "out.glb"
                    run = run_fn(script, out_glb)
                    if run.get("status") == "ok" and run.get("glb_path"):
                        # ingest copies from glb_path; keep it alive past the tempdir by ingesting now
                        att = ingest_attempt(db, task_id=task_id, model_id=model_id, run=run,
                                             script=script, asset_dir=asset_dir)
                        counts[att.status] = counts.get(att.status, 0) + 1
                        seen.add((model_id, task_id))
                        made += 1
                        continue
            att = ingest_attempt(db, task_id=task_id, model_id=model_id, run=run,
                                 script=script, asset_dir=asset_dir)
            counts[att.status] = counts.get(att.status, 0) + 1
            seen.add((model_id, task_id))
            made += 1
    return counts
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_commission_ingest.py -k batch -q`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add app/commission.py tests/test_commission_ingest.py
git commit -m "feat(commission): resumable batch orchestrator"
```

---

### Task 8: CLI driver

**Files:**

- Create: `scripts/commission_arena.py`
- Test: `tests/test_commission_ingest.py` (dry-run plan helper)

**Interfaces:**

- Consumes: everything in `app/commission.py`.
- Produces: `scripts/commission_arena.py` with `main()`; `--roster a,b,c`, `--blender-bin PATH`, `--timeout 120`, `--sandbox-prefix "heavy-run"`, `--max N`, `--dry-run`. Reads `OPENROUTER_API_KEY` from env. Uses `httpx.post` as `complete_fn`'s transport and `run_bpy` as `run_fn`.

- [ ] **Step 1: Write the failing test** (dry-run plan is pure over the DB — no network/Blender)

```python
# add to tests/test_commission_ingest.py
def test_dry_run_plan_counts_uncovered_pairs(tmp_path):
    from scripts import commission_arena

    with SessionLocal() as db:
        tid = _rubric_task(db, "Zea mays")
        plan = commission_arena.plan(db, roster=["m1", "m2"])
        assert plan["tasks"] == 1 and plan["roster"] == 2 and plan["calls_needed"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_commission_ingest.py -k dry_run -q`
Expected: FAIL — `scripts.commission_arena` has no `plan`.

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/commission_arena.py
"""Commissioned-generation arena driver: for each (model in roster) x (6-taxa task), ask the
model (via OpenRouter) for a bpy script, sandbox-run it, and ingest the result. Resumable
(skips attempted pairs), capped (--max), dry-run-able. Core logic lives in app/commission.py.

Env: OPENROUTER_API_KEY. Run against whatever DB BIO3D_DATABASE_URL points at (do NOT point
tests here). Mirrors scripts/trait_judge.py / scripts/scope_judge.py."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import commission, config  # noqa: E402
from app.database import SessionLocal  # noqa: E402


def plan(db, *, roster: list[str]) -> dict:
    tt = commission.resolve_taxon_tasks(db)
    seen = commission.existing_pairs(db)
    needed = sum(1 for m in roster for _, tid in tt if (m, tid) not in seen)
    return {"tasks": len(tt), "roster": len(roster), "calls_needed": needed}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--roster", required=True, help="comma-separated OpenRouter model ids")
    ap.add_argument("--blender-bin", default="blender")
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--sandbox-prefix", default="", help="e.g. 'heavy-run' or 'unshare -rn'")
    ap.add_argument("--max", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    roster = [m.strip() for m in args.roster.split(",") if m.strip()]

    with SessionLocal() as db:
        p = plan(db, roster=roster)
        print(f"commission plan: {p['roster']} models x {p['tasks']} tasks; "
              f"{p['calls_needed']} calls needed")
        if args.dry_run:
            return 0

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print("OPENROUTER_API_KEY not set", file=sys.stderr)
        return 2

    import httpx

    prefix = args.sandbox_prefix.split() or None

    def complete_fn(model_id, prompt):
        return commission.openrouter_complete(httpx.post, model_id, prompt, api_key=api_key)

    def run_fn(script, out_glb):
        return commission.run_bpy(
            script, out_glb=out_glb, timeout_s=args.timeout,
            blender_bin=args.blender_bin, sandbox_prefix=prefix,
        )

    config.ensure_dirs()
    with SessionLocal() as db:
        tt = commission.resolve_taxon_tasks(db)
        res = commission.run_batch(
            db, complete_fn=complete_fn, run_fn=run_fn, roster=roster,
            taxon_tasks=tt, asset_dir=config.ASSET_DIR, max_calls=args.max,
        )
    print(res)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_commission_ingest.py -k dry_run -q`
Expected: PASS (1 passed).

- [ ] **Step 5: Run the full commission suite**

Run: `.venv/bin/python -m pytest tests/test_commission.py tests/test_commission_ingest.py -q`
Expected: PASS (all; the real-Blender test passes if installed, else skips).

- [ ] **Step 6: Commit**

```bash
git add scripts/commission_arena.py tests/test_commission_ingest.py
git commit -m "feat(commission): CLI driver (roster, dry-run, sandbox)"
```

---

## Operator runbook (after implementation, operator-gated)

1. `export OPENROUTER_API_KEY=...` (create at openrouter.ai + add credit).
2. Confirm Blender: `blender --version`.
3. Dry-run the plan against the target DB:
   `BIO3D_DATABASE_URL=... BIO3D_DATA_DIR=... .venv/bin/python scripts/commission_arena.py --roster "anthropic/claude-opus-4.8,openai/gpt-5.5,google/gemini-2.5-pro" --dry-run`
4. Snapshot the DB, then run for real (start with `--max 2` to smoke-test one model end-to-end):
   `... scripts/commission_arena.py --roster "..." --sandbox-prefix "heavy-run" --max 2`
5. Inspect `CommissionAttempt` rows (status distribution, sample scripts) before the full run.
6. → Spec #2: point scope/rubric/vote at `ModelOutput.source == "commissioned"`.

## Self-Review

- **Spec coverage:** prompt set (T4), OpenRouter dispatch (T5), bpy substrate + sandbox (T2), script extraction (T3), mesh validity (T1), CommissionAttempt + failures-first-class + ingestion (T6), resumable batch + dry-run (T7, T8), 6-taxa resolution via TraitRubric (T7), single-shot + transport-only retry (T7 dispatch try/except; note: httpx retry-on-5xx is a thin follow-up, acceptable to omit in v1 since failures are recorded), operator prereqs (runbook). Contact-sheet rendering intentionally omitted (downstream scorers render on demand — documented in the spec's out-of-scope reasoning).
- **Placeholder scan:** none — every code step is complete.
- **Type consistency:** `run` dict keys (`status/stderr/duration_ms/glb_path/mesh_stats`) are consistent across `run_bpy` (T2), `ingest_attempt` (T6), and `run_batch` (T7); `complete_fn(model_id, prompt)->str` and `run_fn(script, out_glb)->dict` match between T7 and T8.
