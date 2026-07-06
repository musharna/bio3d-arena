# tests/test_score_semantic_script.py
import scripts.score_semantic as ss


def test_condition_is_turntable_for_cache_reuse():
    # Must match app.completeness / score_completeness so cached sheets are reused.
    assert ss.CONDITION == "turntable"


def test_sheet_provider_reuses_cached_without_rendering(tmp_path, monkeypatch):
    from app import config

    # A cached turntable sheet must be read WITHOUT spawning a render subprocess (post-PR#18
    # architecture: _sheet_provider() takes no args and renders each uncached output in a
    # timeout-guarded subprocess; a cached sheet short-circuits before any Popen).
    monkeypatch.setattr(config, "ASSET_DIR", tmp_path)
    renders = tmp_path / "renders"
    renders.mkdir()
    (renders / "7_turntable.png").write_bytes(b"CACHED")

    def no_popen(*a, **k):
        raise AssertionError("must not render when the sheet is cached")

    monkeypatch.setattr(ss.subprocess, "Popen", no_popen)

    sheet_for = ss._sheet_provider()
    assert sheet_for(7) == b"CACHED"
