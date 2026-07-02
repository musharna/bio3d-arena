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
