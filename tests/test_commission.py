from __future__ import annotations

import shutil

import pytest
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
    res = commission.run_bpy(
        "print('x')", out_glb=tmp_path / "o.glb", blender_bin="definitely-not-blender"
    )
    assert res["status"] == "error" and res["glb_path"] is None


def test_sandbox_env_strips_secrets_keeps_essentials():
    base = {
        "PATH": "/usr/bin",
        "HOME": "/home/u",
        "OPENROUTER_API_KEY": "sk-x",
        "ANTHROPIC_API_KEY": "sk-y",
        "BIO3D_DATABASE_URL": "sqlite:///study.db",
        "MY_TOKEN": "t",
        "APP_SECRET": "s",
    }
    env = commission._sandbox_env("/tmp/out.glb", base_env=base)
    assert env["PATH"] == "/usr/bin" and env["HOME"] == "/home/u"
    assert env["OUT_GLB"] == "/tmp/out.glb"
    for leaked in (
        "OPENROUTER_API_KEY",
        "ANTHROPIC_API_KEY",
        "BIO3D_DATABASE_URL",
        "MY_TOKEN",
        "APP_SECRET",
    ):
        assert leaked not in env


def test_extract_script_fenced_python():
    txt = "Here it is:\n```python\nimport bpy\nprint(1)\n```\nDone."
    assert commission.extract_script(txt) == "import bpy\nprint(1)"


def test_extract_script_plain_fence_and_unfenced_and_empty():
    assert commission.extract_script("```\nimport bpy\n```") == "import bpy"
    assert commission.extract_script("import bpy\nx=1") == "import bpy\nx=1"
    assert commission.extract_script("") == ""
    assert commission.extract_script(None) == ""


def test_extract_script_unclosed_fence_is_stripped():
    # A truncated/unterminated completion opens ```python but never closes it — the opening
    # fence line must still be stripped so the script doesn't die on line 1 (the Gemini bug).
    txt = "```python\nimport bpy\nbpy.ops.mesh.primitive_cube_add()"
    assert commission.extract_script(txt) == "import bpy\nbpy.ops.mesh.primitive_cube_add()"


def test_build_prompt_pins_contract():
    p = commission.build_prompt("Solanum lycopersicum", "tomato")
    assert "OUT_GLB" in p and "tomato" in p and "Solanum lycopersicum" in p
    assert "bpy" in p.lower()
    assert "4.2" in p  # pins the target Blender version so models use the right bpy API


def test_species_common_covers_six_taxa():
    assert set(commission.SPECIES_COMMON) == {
        "Solanum lycopersicum",
        "Zea mays",
        "Pinus sylvestris",
        "Rosa",
        "Glycine max",
        "Arabidopsis thaliana",
    }


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


def test_openrouter_complete_retries_transient_then_succeeds():
    calls = {"n": 0}
    slept = []

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}]}

    def flaky_post(url, headers=None, json=None, timeout=None):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("transient")
        return _Resp()

    out = commission.openrouter_complete(
        flaky_post, "m", "p", api_key="k", max_retries=3, sleep_fn=lambda s: slept.append(s)
    )
    assert out == "ok" and calls["n"] == 3 and len(slept) == 2


def test_openrouter_complete_raises_after_exhausting_retries():
    import pytest

    def always_fail(url, headers=None, json=None, timeout=None):
        raise RuntimeError("down")

    with pytest.raises(RuntimeError):
        commission.openrouter_complete(
            always_fail, "m", "p", api_key="k", max_retries=2, sleep_fn=lambda s: None
        )
