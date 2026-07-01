from __future__ import annotations


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
