"""Commissioned-generation arena harness (core).

Give each competing LLM the same plant-generation task, run the bpy script it writes in a
sandbox, and ingest the resulting mesh as an agent-attributed arena output. Pure/core helpers
here (prompt build, script extraction, OpenRouter dispatch, sandbox run, mesh validation, DB
ingestion); scripts/commission_arena.py wires the real HTTP client + Blender."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

import trimesh


_SECRET_ENV_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD")
_SECRET_ENV_EXACT = {"BIO3D_DATABASE_URL"}


def _sandbox_env(out_glb, base_env=None) -> dict:
    """Environment for the untrusted bpy subprocess: inherit the parent env MINUS any
    secret-looking vars (name contains KEY/TOKEN/SECRET/PASSWORD, or is BIO3D_DATABASE_URL),
    then inject OUT_GLB. Keeps PATH/HOME/etc. so Blender still runs."""
    src = os.environ if base_env is None else base_env
    out = {
        k: v
        for k, v in src.items()
        if k not in _SECRET_ENV_EXACT and not any(m in k.upper() for m in _SECRET_ENV_MARKERS)
    }
    out["OUT_GLB"] = str(out_glb)
    return out


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


def run_bpy(
    script_text: str,
    *,
    out_glb,
    timeout_s: int = 120,
    blender_bin: str = "blender",
    sandbox_prefix: list[str] | None = None,
) -> dict:
    """Run an LLM-authored bpy script headless in a throwaway temp cwd with a sandboxed
    environment. Injects OUT_GLB into the environment and strips secret-looking vars
    (containing KEY/TOKEN/SECRET/PASSWORD, or BIO3D_DATABASE_URL). Returns a status dict;
    never raises on script failure. sandbox_prefix lets the caller wrap the command
    (e.g. ["heavy-run"] for a memory cap, ["unshare","-rn"] for no network) — kept
    configurable so tests run bare."""
    out_glb = Path(out_glb)
    prefix = list(sandbox_prefix or [])
    start = time.monotonic()
    with tempfile.TemporaryDirectory() as td:
        script_path = Path(td) / "gen.py"
        script_path.write_text(script_text)
        env = _sandbox_env(out_glb)
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


def extract_script(text: str) -> str:
    """Pull the Python script out of a chat completion. Single fenced block, literal
    terminator — no nested/ambiguous quantifiers (safe on arbitrary completions)."""
    if not text:
        return ""
    m = re.search(r"```(?:python)?[ \t]*\n(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return text.strip()
