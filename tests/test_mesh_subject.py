"""Subject-isolation guard: drop stray scenery ground/floor planes that an
LLM-authored Blender script leaves in a commissioned scene, while never
touching real (even flat) organism geometry.

The failure this guards against: a code-gen model (e.g. grok-4.20) adds a large
horizontal ``Plane``/``Soil`` the organism sits on. It renders as a slab
through the subject in the turntable both humans and the VLM judge score.
"""

from __future__ import annotations

import numpy as np
import trimesh

from app import mesh_subject


def _plane(half_x: float, half_z: float, y: float = 0.0) -> trimesh.Trimesh:
    """A flat horizontal quad (2 tris) of size 2*half_x by 2*half_z at height y."""
    return trimesh.Trimesh(
        vertices=[
            [-half_x, y, -half_z],
            [half_x, y, -half_z],
            [half_x, y, half_z],
            [-half_x, y, half_z],
        ],
        faces=[[0, 1, 2], [0, 2, 3]],
    )


def _scene(**named):
    s = trimesh.Scene()
    for name, geom in named.items():
        s.add_geometry(geom, geom_name=name)
    return s


# --- the core case: a big floor under a compact organism -------------------


def test_names_a_large_ground_plane_under_the_organism():
    # grok-4.20 Boletus: Sphere ~2.7^3 + Plane 6x6 floor.
    s = _scene(Sphere=trimesh.creation.icosphere(radius=1.35), Plane=_plane(3, 3))
    assert mesh_subject.scenery_plane_names(s) == ["Plane"]


def test_soil_named_floor_is_named():
    s = _scene(Body=trimesh.creation.icosphere(radius=1.0), Soil=_plane(5, 5))
    assert mesh_subject.scenery_plane_names(s) == ["Soil"]


# --- false-positive protection: real anatomy must survive ------------------


def test_keeps_a_large_flat_mesh_that_is_not_scenery_named():
    # A butterfly wing: large, flat, but named anatomy — never a candidate.
    s = _scene(Body=trimesh.creation.box((0.4, 0.4, 2.0)), Wing_Fore_L=_plane(2.3, 1.7))
    assert mesh_subject.scenery_plane_names(s) == []


def test_keeps_a_small_plane_named_part_smaller_than_the_organism():
    # A gill/scale the model built from a Plane primitive: named "Plane" but
    # far smaller than the real organism mesh — it IS the organism, keep it.
    s = _scene(Cap=trimesh.creation.icosphere(radius=1.0), Plane=_plane(0.1, 0.1))
    assert mesh_subject.scenery_plane_names(s) == []


def test_strips_nothing_when_three_or_more_flat_planes_exist():
    # Anatomy built from many flat planes (an Arabidopsis rosette, a fan of
    # gills): more than two flat scenery-named planes is not a floor, so leave
    # them all for manual review rather than risk stripping the organism.
    s = _scene(
        Stem=trimesh.creation.box((0.3, 2.0, 0.3)),
        Plane=_plane(1, 1),
        **{"Plane.001": _plane(1, 1), "Plane.002": _plane(1, 1)},
    )
    assert mesh_subject.scenery_plane_names(s) == []


def test_clean_organism_has_no_candidates():
    s = _scene(Stem=trimesh.creation.box((1, 2, 1)), Cap=trimesh.creation.icosphere(radius=0.8))
    assert mesh_subject.scenery_plane_names(s) == []


def test_ignores_a_plane_rotated_vertical_in_world_space():
    # A petal a model built from a Plane primitive and rotated upright: flat in
    # its LOCAL frame but a vertical sheet in the scene. Judged in world space,
    # it is not a horizontal floor — keep it. (Guards the local-vs-world bug.)
    upright = trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0])
    s = trimesh.Scene()
    s.add_geometry(trimesh.creation.icosphere(radius=1.0), geom_name="Body")
    s.add_geometry(_plane(1.5, 1.5), geom_name="Plane", transform=upright)
    assert mesh_subject.scenery_plane_names(s) == []


# --- GLB round-trip: strip + atomic rewrite + verify -----------------------


def test_strip_glb_removes_the_floor_and_keeps_the_organism(tmp_path):
    s = _scene(Sphere=trimesh.creation.icosphere(radius=1.35), Plane=_plane(3, 3))
    glb = tmp_path / "grok_boletus.glb"
    s.export(str(glb))

    res = mesh_subject.strip_scenery_from_glb(glb, apply=True)
    assert res["action"] == "stripped"
    assert res["stripped"] == ["Plane"]

    reloaded = trimesh.load(str(glb), force="scene")
    assert set(reloaded.geometry) == {"Sphere"}
    assert mesh_subject.scenery_plane_names(reloaded) == []


def test_strip_glb_dry_run_does_not_write(tmp_path):
    s = _scene(Sphere=trimesh.creation.icosphere(radius=1.35), Plane=_plane(3, 3))
    glb = tmp_path / "dry.glb"
    s.export(str(glb))
    before = glb.read_bytes()

    res = mesh_subject.strip_scenery_from_glb(glb, apply=False)
    assert res["action"] == "would_strip"
    assert res["stripped"] == ["Plane"]
    assert glb.read_bytes() == before  # untouched


def test_strip_glb_is_idempotent_and_backs_up(tmp_path):
    s = _scene(Sphere=trimesh.creation.icosphere(radius=1.35), Plane=_plane(3, 3))
    glb = tmp_path / "once.glb"
    s.export(str(glb))
    backups = tmp_path / "bak"

    first = mesh_subject.strip_scenery_from_glb(glb, apply=True, backup_dir=backups)
    assert first["action"] == "stripped"
    assert (backups / "once.glb").exists()

    second = mesh_subject.strip_scenery_from_glb(glb, apply=True, backup_dir=backups)
    assert second["action"] == "skip_no_scenery"


# --- targeted strip: pedestals the scenery classifier structurally cannot see -


def _pedestal(half: float = 1.5, thickness: float = 0.6) -> trimesh.Trimesh:
    """A chunky display plinth — thick and blockish, not a thin floor quad."""
    return trimesh.creation.box((2 * half, thickness, 2 * half))


def test_scenery_classifier_does_not_see_a_pedestal():
    """Why strip_named_geometry_from_glb exists (live audit 2026-07-25).

    Six outputs stand on a plinth the ground-plane classifier misses on three
    independent axes AT ONCE: primitive name (``Cube.001``/``Cylinder`` vs the
    scenery regex), face count (up to 5172 vs _FACE_CAP=200), and thickness
    (0.32-0.42 of footprint vs _FLAT_RATIO=0.10). Widening the classifier far
    enough to reach them also reaches a dog's torso — an actual false positive
    that audit produced — so verified pedestals are stripped by explicit NAME
    and the classifier stays conservative. This test pins that gap open on
    purpose: if it ever starts failing, the classifier has been widened and the
    false-positive risk needs re-examining.
    """
    s = _scene(Dome=trimesh.creation.icosphere(radius=0.5), **{"Cube.001": _pedestal()})
    assert mesh_subject.scenery_plane_names(s) == []


def test_strip_named_removes_only_the_named_geometry(tmp_path):
    s = _scene(Dome=trimesh.creation.icosphere(radius=0.5), **{"Cube.001": _pedestal()})
    glb = tmp_path / "hericium.glb"
    s.export(str(glb))

    res = mesh_subject.strip_named_geometry_from_glb(glb, ["Cube.001"], apply=True)
    assert res["action"] == "stripped"
    assert res["stripped"] == ["Cube.001"]

    reloaded = trimesh.load(str(glb), force="scene")
    assert set(reloaded.geometry) == {"Dome"}


def test_strip_named_dry_run_does_not_write(tmp_path):
    s = _scene(Dome=trimesh.creation.icosphere(radius=0.5), **{"Cube.001": _pedestal()})
    glb = tmp_path / "dry.glb"
    s.export(str(glb))
    before = glb.read_bytes()

    res = mesh_subject.strip_named_geometry_from_glb(glb, ["Cube.001"], apply=False)
    assert res["action"] == "would_strip"
    assert res["stripped"] == ["Cube.001"]
    assert glb.read_bytes() == before


def test_strip_named_is_idempotent_and_backs_up(tmp_path):
    """Re-running must be a no-op, so a half-finished batch can be resumed."""
    s = _scene(Dome=trimesh.creation.icosphere(radius=0.5), **{"Cube.001": _pedestal()})
    glb = tmp_path / "once.glb"
    s.export(str(glb))
    backups = tmp_path / "bak"

    first = mesh_subject.strip_named_geometry_from_glb(
        glb, ["Cube.001"], apply=True, backup_dir=backups
    )
    assert first["action"] == "stripped"
    assert (backups / "once.glb").exists()

    second = mesh_subject.strip_named_geometry_from_glb(
        glb, ["Cube.001"], apply=True, backup_dir=backups
    )
    assert second["action"] == "skip_absent"


def test_strip_named_refuses_to_empty_the_scene(tmp_path):
    """The hard invariant: never strip everything, however the caller asks."""
    s = _scene(**{"Cube.001": _pedestal()})
    glb = tmp_path / "only.glb"
    s.export(str(glb))
    before = glb.read_bytes()

    res = mesh_subject.strip_named_geometry_from_glb(glb, ["Cube.001"], apply=True)
    assert res["action"] == "skip_would_empty"
    assert glb.read_bytes() == before


def test_strip_named_ignores_names_that_are_not_present(tmp_path):
    """A stale name in the strip list must not take the organism with it."""
    s = _scene(Dome=trimesh.creation.icosphere(radius=0.5), **{"Cube.001": _pedestal()})
    glb = tmp_path / "partial.glb"
    s.export(str(glb))

    res = mesh_subject.strip_named_geometry_from_glb(glb, ["Cube.001", "NotThere"], apply=True)
    assert res["action"] == "stripped"
    assert res["stripped"] == ["Cube.001"]
    assert set(trimesh.load(str(glb), force="scene").geometry) == {"Dome"}
