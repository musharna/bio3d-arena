from __future__ import annotations

import io

from PIL import Image

from app import judge_render
from app.database import SessionLocal, init_db
from app.models import Category, Generator, ModelOutput, Task


def setup_module(_m):
    init_db()


def _png(color, size=64) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (size, size), color).save(buf, format="PNG")
    return buf.getvalue()


def test_contact_sheet_path_convention():
    assert judge_render.contact_sheet_path(42, "multi4") == "renders/42_multi4.png"


def test_tile_contact_sheet_dimensions_2x2():
    tiles = [_png(c, 64) for c in ("red", "green", "blue", "white")]
    out = judge_render.tile_contact_sheet(tiles, cols=2, rows=2)
    img = Image.open(io.BytesIO(out))
    assert img.size == (128, 128)  # 2*64 x 2*64


def test_render_contact_sheets_writes_file_and_is_idempotent(tmp_path, monkeypatch):
    import app.config as config

    monkeypatch.setattr(config, "ASSET_DIR", tmp_path)
    calls = {"n": 0}

    def fake_capture(glb_abs, azimuths, elev):
        calls["n"] += 1
        return [_png("red", 64) for _ in azimuths]

    with SessionLocal() as db:
        cat = Category(slug="jr-cat", name="C")
        db.add(cat)
        db.flush()
        task = Task(category_id=cat.id, title="jr-task", prompt="p")
        gen = Generator(slug="jr-gen", name="G")
        db.add_all([task, gen])
        db.flush()
        (tmp_path / "seed").mkdir(parents=True, exist_ok=True)
        (tmp_path / "seed" / "x.glb").write_bytes(b"glTF-stub")
        out = ModelOutput(task_id=task.id, generator_id=gen.id, asset_path="seed/x.glb")
        db.add(out)
        db.commit()

        res = judge_render.render_contact_sheets(db, [out.id], "multi4", capture_multi=fake_capture)
        assert res == {"rendered": 1, "errors": 0}
        sheet = tmp_path / judge_render.contact_sheet_path(out.id, "multi4")
        assert sheet.exists() and sheet.stat().st_size > 0
        # Idempotent: second call skips (no new capture).
        res2 = judge_render.render_contact_sheets(
            db, [out.id], "multi4", capture_multi=fake_capture
        )
        assert res2["rendered"] == 0
        assert calls["n"] == 1
