# tests/test_generate_multiview_recon.py
from __future__ import annotations

import json
from pathlib import Path

import trimesh
from sqlalchemy import select

from app import config
from app.database import SessionLocal, init_db
from app.models import Category, ModelOutput, Task

from scripts.generate_multiview_recon import run_subject


def _write_stub_ref(ref_rel: str) -> None:
    """Write a minimal JPEG stub to config.ASSET_DIR/<ref_rel> so run_subject finds the ref."""
    p = Path(config.ASSET_DIR) / ref_rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\xff\xd8\xff\xd9")


def setup_module(_m):
    init_db()


PINE = "Pinus sylvestris — single-image → 3D reconstruction"


def _seed(db):
    db.query(ModelOutput).filter(ModelOutput.source.like("recon:%mvt%")).delete(
        synchronize_session=False
    )
    db.query(Task).filter_by(title=PINE).delete(synchronize_session=False)
    cat = db.query(Category).filter_by(slug="plants").first() or Category(slug="plants", name="P")
    db.add(cat)
    db.flush()
    db.add(Task(category_id=cat.id, title=PINE, prompt="p"))
    db.commit()


def _box():
    return trimesh.creation.box().export(file_type="glb")


def test_run_subject_nvs_then_multiview(tmp_path):
    _write_stub_ref("reference/arabidopsis_ref.jpg")
    with SessionLocal() as db:
        _seed(db)
        calls = {}

        def fake_nvs(image_bytes, *, api_key):
            calls["nvs"] = True
            return [b"v%d" % i for i in range(6)]

        def fake_mv(views, *, api_key):
            calls["mv_n"] = len(views)
            return _box()

        mv = {"recon:trellis-mv-mvt": (fake_mv, "FAL_KEY", "TRELLIS mv")}
        subj = {"ref": "reference/arabidopsis_ref.jpg", "task_title": PINE}
        res = run_subject(
            db,
            subj,
            env={"REPLICATE_API_TOKEN": "r", "FAL_KEY": "f"},
            nvs_fn=fake_nvs,
            mv_providers=mv,
            views_dir=tmp_path,
        )
        assert calls["nvs"] and calls["mv_n"] == 6
        assert res["n_views"] == 6 and res["recon"]["generated"] == 1
        out = (
            db.execute(select(ModelOutput).where(ModelOutput.source == "recon:trellis-mv-mvt"))
            .scalars()
            .one()
        )
        assert json.loads(out.meta_json)["modality"] == "multiview"
        # views were cached to disk
        assert len(list(tmp_path.glob("*.png"))) == 6


def test_run_subject_uses_cached_views_without_calling_nvs(tmp_path):
    """Cache hit: ≥2 cached view_*.png present → reuse them, never call NVS, no token needed."""
    _write_stub_ref("reference/arabidopsis_ref.jpg")
    for i in range(6):
        (tmp_path / f"view_{i}.png").write_bytes(b"cached%d" % i)
    with SessionLocal() as db:
        _seed(db)
        calls = {}

        def fake_nvs(image_bytes, *, api_key):
            raise AssertionError("NVS must not be called on a cache hit")

        def fake_mv(views, *, api_key):
            calls["mv_views"] = list(views)
            return _box()

        mv = {"recon:trellis-mv-mvtc": (fake_mv, "FAL_KEY", "TRELLIS mv")}
        subj = {"ref": "reference/arabidopsis_ref.jpg", "task_title": PINE}
        res = run_subject(
            db,
            subj,
            env={"FAL_KEY": "f"},  # no REPLICATE_API_TOKEN — cache hit must not need it
            nvs_fn=fake_nvs,
            mv_providers=mv,
            views_dir=tmp_path,
        )
        assert res["n_views"] == 6 and res["cached"] is True
        # views loaded in numeric index order, not lexicographic
        assert calls["mv_views"] == [b"cached%d" % i for i in range(6)]


def test_run_subject_refresh_ignores_cache(tmp_path):
    """--refresh: regenerate via NVS even when cached views exist."""
    _write_stub_ref("reference/arabidopsis_ref.jpg")
    for i in range(6):
        (tmp_path / f"view_{i}.png").write_bytes(b"stale%d" % i)
    with SessionLocal() as db:
        _seed(db)
        calls = {}

        def fake_nvs(image_bytes, *, api_key):
            calls["nvs"] = True
            return [b"fresh%d" % i for i in range(6)]

        def fake_mv(views, *, api_key):
            calls["mv_views"] = list(views)
            return _box()

        mv = {"recon:trellis-mv-mvtr": (fake_mv, "FAL_KEY", "TRELLIS mv")}
        subj = {"ref": "reference/arabidopsis_ref.jpg", "task_title": PINE}
        res = run_subject(
            db,
            subj,
            env={"REPLICATE_API_TOKEN": "r", "FAL_KEY": "f"},
            nvs_fn=fake_nvs,
            mv_providers=mv,
            views_dir=tmp_path,
            refresh=True,
        )
        assert calls.get("nvs") is True and res["cached"] is False
        assert calls["mv_views"] == [b"fresh%d" % i for i in range(6)]
        # cache rewritten with fresh views
        assert (tmp_path / "view_0.png").read_bytes() == b"fresh0"


def test_run_subject_skips_when_nvs_too_few():
    _write_stub_ref("reference/arabidopsis_ref.jpg")
    with SessionLocal() as db:
        _seed(db)

        def fake_nvs(image_bytes, *, api_key):
            return [b"only-one"]

        subj = {"ref": "reference/arabidopsis_ref.jpg", "task_title": PINE}
        res = run_subject(
            db,
            subj,
            env={"REPLICATE_API_TOKEN": "r", "FAL_KEY": "f"},
            nvs_fn=fake_nvs,
            mv_providers={"recon:x": (lambda *a, **k: _box(), "FAL_KEY", "x")},
            views_dir=None,
        )
        assert "skipped" in res
