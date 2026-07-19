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

from .commission import build_prompt, extract_script
from .organ_inventory import inventory_for

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
_SECRET_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD")

# Trusted (our own) headless render: import the GLB at IN_GLB, frame all mesh geometry with a
# 3/4 camera + sun over a dark world, render a 512² PNG to OUT_PNG. Uses Blender's default engine.
RENDER_SCRIPT = r"""
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
"""


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
            paradigm="agentic",
        )
        db.add(gen)
        db.flush()
    elif not gen.paradigm:
        # Heal a generator born blank before this fix — the agentic leaderboard filters
        # paradigm == 'agentic', so a blank one is invisible on its own board (the same defect
        # PR #70 fixed for the commissioned/procedural_llm path, on the agentic helper it missed).
        gen.paradigm = "agentic"
    return gen


def critique_prompt(species: str, common: str) -> str:
    """Ask the model to critique its own render against the real organism.

    Organism-neutral, and it names the specimen's actual body plan from ORGAN_INVENTORY (with
    complements — a goldfish has TWO pectoral fins, a monarch four wings and six legs), so the
    critique has something concrete to check against. It used to say "{common} plant" and ask
    about "leaf/needle shape", which is unanswerable for a fungus or a fish."""
    inv = inventory_for(species)
    if inv is None:
        anatomy = "every structure it should have"
    else:
        parts = [
            f"{o.visual} (×{o.complement})" if o.complement > 1 else o.visual
            for o in inv.organs
            if o.required
        ]
        anatomy = "; ".join(parts)
    return (
        f"The attached image is a render of YOUR current 3D mesh of a {common} "
        f"({species}), built by your previous Blender-Python script. Critically compare it to a "
        f"real {common}: name what is wrong or missing (proportions, topology, obvious artefacts, "
        f"and any missing or miscounted anatomy — a specimen should show {anatomy}). Then output "
        "ONLY an improved, COMPLETE Blender 4.2 bpy script that fixes those issues. Leave the "
        # Not "…and leaves the specimen in the scene": a fungus critique may not contain plant
        # vocabulary, and "leaves" is on that list. test_agentic_critique_is_organism_neutral.
        "finished specimen in the scene — saving it is handled for you, so do not write any file "
        "and do not clear the scene at the end. No explanation, no markdown."
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
                new_script = extract_script(vision_fn(critique_prompt(species, common), png))
                run2 = run_fn(new_script, str(Path(td) / f"iter{i}.glb"))
            except Exception:  # noqa: BLE001 — any revise-round failure stops refinement, keeps best mesh
                break
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
