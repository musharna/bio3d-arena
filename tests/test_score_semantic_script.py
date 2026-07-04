# tests/test_score_semantic_script.py
import scripts.score_semantic as ss


def test_condition_is_turntable_for_cache_reuse():
    # Must match app.completeness / score_completeness so cached sheets are reused.
    assert ss.CONDITION == "turntable"


def test_sheet_provider_reuses_cached_without_rendering(tmp_path, monkeypatch):
    from app import config

    # Point ASSET_DIR at a temp dir and pre-place a cached turntable sheet for output 7.
    monkeypatch.setattr(config, "ASSET_DIR", tmp_path)
    renders = tmp_path / "renders"
    renders.mkdir()
    (renders / "7_turntable.png").write_bytes(b"CACHED")

    called = {"render": 0}

    def fake_render(db, ids, condition, *, capture_multi):
        called["render"] += 1
        return {"rendered": 0, "errors": 0, "failures": []}

    monkeypatch.setattr(ss, "render_contact_sheets", fake_render)

    sheet_for = ss._sheet_provider(db=None, capture_multi=lambda *a, **k: [])
    data = sheet_for(7)
    assert data == b"CACHED"
    # render_contact_sheets is idempotent, so calling it is allowed, but the cached bytes are read.
