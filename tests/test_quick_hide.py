"""Curator quick-hide: on the internal instance the ⚑ hides the output (flag threshold 1) and
the arena advances to a fresh comparison instead of leaving the just-hidden model on screen.
Served-asset wiring guard, mirroring tests/test_flag_client.py."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_flag_advances_after_hide():
    ajs = client.get("/static/arena.js").text
    # flagOutput advances (loadNext) after a successful /api/flag, and reads as a hide.
    assert "/api/flag" in ajs
    assert "Hide this output from the arena" in ajs
    assert "loadNext()" in ajs


def test_viewer_flag_button_reads_as_hide():
    vjs = client.get("/static/viewer.js").text
    assert "Hide this output from the arena" in vjs
