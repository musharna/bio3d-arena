# tests/test_judge_capture_live.py
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app import judge_render
from app.database import init_db


def setup_module(_m):
    init_db()  # no-op on real DB; creates schema on empty worktree DB


def _has_browser() -> bool:
    try:
        import playwright  # noqa: F401
    except Exception:
        return False
    return shutil.which("chromium") is not None or True  # playwright ships its own


@pytest.mark.skipif(not _has_browser(), reason="playwright/chromium not available")
def test_real_multi4_render_of_a_seeded_glb(tmp_path, monkeypatch):
    """Real-execution check: render one real seeded GLB to a multi4 contact sheet."""
    import app.config as config
    from app.database import SessionLocal
    from app.models import Category, Generator, ModelOutput, Task
    from scripts.judge_capture import browser_capture_multi_factory

    # conftest isolates BIO3D_DATA_DIR into a temp dir holding only reference photos, so the
    # first ModelOutput in the test DB points at an asset that does not exist there. This test
    # used to grab that row, get an error, and skip("render errored ... not a logic failure") --
    # meaning the "real-execution check" never once exercised the renderer, and a genuine render
    # regression would have looked exactly like a green suite. Copy a REAL GLB in and render it;
    # skip only when the machine genuinely has no real asset to render.
    real_glb = next(
        (
            p
            for d in ("seed", "commissioned", "agentic")
            for p in sorted(
                (Path(__file__).resolve().parent.parent / "data/assets" / d).glob("*.glb")
            )
        ),
        None,
    )
    if real_glb is None:
        pytest.skip("no real GLB available on this machine (gitignored data/assets absent)")

    with SessionLocal() as db:
        dest_rel = f"seed/{real_glb.name}"
        dest = config.ASSET_DIR / dest_rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(real_glb, dest)
        cat = Category(slug="jcl-cat", name="C")
        gen = Generator(slug="jcl-gen", name="G")
        db.add_all([cat, gen])
        db.flush()
        task = Task(category_id=cat.id, title="jcl-task", prompt="p")
        db.add(task)
        db.flush()
        out = ModelOutput(
            task_id=task.id, generator_id=gen.id, asset_path=dest_rel, asset_format="glb"
        )
        db.add(out)
        db.flush()

        capture_multi = browser_capture_multi_factory()
        res = judge_render.render_contact_sheets(
            db, [out.id], "multi4", capture_multi=capture_multi
        )
        # Fail loud: the asset is present and playwright is importable, so an error here is a
        # renderer defect, not an environment quirk.
        assert res["errors"] == 0, f"render failed on a real GLB: {res}"
        assert res["rendered"] == 1, res
        sheet = config.ASSET_DIR / judge_render.contact_sheet_path(out.id, "multi4")
        assert sheet.exists() and sheet.stat().st_size > 1000
