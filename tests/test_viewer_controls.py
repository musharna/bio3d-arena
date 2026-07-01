# tests/test_viewer_controls.py
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_viewer_js_has_controls():
    vjs = client.get("/static/viewer.js").text
    # toolbar builder + fullscreen toggle exist
    assert "function addControls" in vjs
    assert "function toggleFullscreen" in vjs
    # toolbar markup + a11y labels
    assert "viewer-controls" in vjs
    assert "viewer-ctl" in vjs
    assert "Reset view" in vjs
    assert "Fullscreen" in vjs
    # reset closure + molecular resize hook + fullscreen API + single listener
    assert "_resetView" in vjs
    assert "_onResize" in vjs
    assert "requestFullscreen" in vjs
    assert "fullscreenchange" in vjs


def test_style_css_has_control_rules():
    css = client.get("/static/style.css").text
    assert ".viewer-controls" in css
    assert ".viewer-ctl" in css
