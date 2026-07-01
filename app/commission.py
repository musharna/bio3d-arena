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
