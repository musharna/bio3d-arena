"""Procedural GLB asset generation for demo/seed data.

Builds simple, visibly-distinct biological-ish meshes with trimesh and exports
them to GLB so the arena viewer has real 3D content out of the box. Geometry is
varied deterministically by a seed so different generators produce different
shapes for the same task.

This is demo scaffolding — real deployments upload generator outputs via /admin.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import trimesh


def _color(rgb: tuple[int, int, int]) -> list[int]:
    return [rgb[0], rgb[1], rgb[2], 255]


def _jitter(mesh: trimesh.Trimesh, scale: float, rng: np.random.Generator) -> None:
    """Displace vertices by small random noise — makes 'organic' variation."""
    mesh.vertices += rng.normal(0.0, scale, size=mesh.vertices.shape)


def build_asset(shape: str, seed: int, out_path: Path) -> dict:
    """Build a GLB for the given biological shape and seed. Returns provenance meta."""
    rng = np.random.default_rng(seed)
    scene = trimesh.Scene()

    if shape == "cell":
        body = trimesh.creation.icosphere(subdivisions=rng.integers(2, 4), radius=1.0)
        _jitter(body, 0.03, rng)
        body.visual.vertex_colors = _color((120, 200, 140))
        scene.add_geometry(body, node_name="membrane")
        # nucleus
        nucleus = trimesh.creation.icosphere(subdivisions=2, radius=0.4)
        nucleus.apply_translation([0.2, 0.1, 0.0])
        nucleus.visual.vertex_colors = _color((90, 110, 200))
        scene.add_geometry(nucleus, node_name="nucleus")

    elif shape == "flower":
        # central receptacle
        center = trimesh.creation.icosphere(subdivisions=2, radius=0.35)
        center.visual.vertex_colors = _color((230, 200, 70))
        scene.add_geometry(center, node_name="center")
        n_petals = int(rng.integers(5, 9))
        hue = rng.choice([(220, 90, 120), (200, 120, 220), (240, 150, 80)])
        for i in range(n_petals):
            ang = 2 * np.pi * i / n_petals
            petal = trimesh.creation.icosphere(subdivisions=2, radius=0.5)
            petal.apply_scale([0.55, 0.22, 0.9])
            _jitter(petal, 0.02, rng)
            petal.apply_translation([0.7 * np.cos(ang), 0.0, 0.7 * np.sin(ang)])
            petal.visual.vertex_colors = _color(tuple(int(x) for x in hue))
            scene.add_geometry(petal, node_name=f"petal_{i}")

    elif shape == "root":
        # a stem with branching roots (cylinders)
        for i in range(int(rng.integers(4, 8))):
            length = float(rng.uniform(0.8, 1.6))
            seg = trimesh.creation.cylinder(
                radius=rng.uniform(0.04, 0.1), height=length, sections=8
            )
            seg.visual.vertex_colors = _color((180, 150, 110))
            ang = rng.uniform(0, 2 * np.pi)
            tilt = rng.uniform(-0.6, 0.6)
            T = trimesh.transformations.rotation_matrix(tilt, [np.cos(ang), 0, np.sin(ang)])
            seg.apply_transform(T)
            seg.apply_translation([rng.uniform(-0.3, 0.3), -length / 2, rng.uniform(-0.3, 0.3)])
            scene.add_geometry(seg, node_name=f"root_{i}")

    elif shape == "protein":
        # a backbone path of spheres (residues) joined by bonds
        n = int(rng.integers(8, 16))
        pts = np.cumsum(rng.normal(0, 0.4, size=(n, 3)), axis=0)
        for i, p in enumerate(pts):
            res = trimesh.creation.icosphere(subdivisions=1, radius=0.18)
            res.apply_translation(p)
            res.visual.vertex_colors = _color((200, 80, 80) if i % 2 else (80, 160, 200))
            scene.add_geometry(res, node_name=f"res_{i}")

    elif shape == "molecule":
        # a few atoms with bonds
        n = int(rng.integers(3, 6))
        centers = rng.uniform(-0.8, 0.8, size=(n, 3))
        for i, c in enumerate(centers):
            atom = trimesh.creation.icosphere(subdivisions=2, radius=rng.uniform(0.2, 0.35))
            atom.apply_translation(c)
            atom.visual.vertex_colors = _color((220, 220, 230) if i == 0 else (200, 100, 60))
            scene.add_geometry(atom, node_name=f"atom_{i}")

    else:  # generic blob fallback
        body = trimesh.creation.icosphere(subdivisions=3, radius=1.0)
        _jitter(body, 0.08, rng)
        body.visual.vertex_colors = _color((160, 160, 180))
        scene.add_geometry(body, node_name="blob")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    scene.export(out_path, file_type="glb")
    vtx = int(sum(len(g.vertices) for g in scene.geometry.values()))
    return {"shape": shape, "seed": int(seed), "vertices": vtx, "generated": True}
