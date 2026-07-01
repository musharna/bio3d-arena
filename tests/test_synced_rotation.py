# tests/test_synced_rotation.py
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_syncpair_defined_and_wired():
    vjs = client.get("/static/viewer.js").text
    assert "function syncPair" in vjs
    assert "syncPair" in vjs and "Bio3DViewer" in vjs  # exported
    # gated on user-interaction (feedback-safe)
    assert "user-interaction" in vjs
    ajs = client.get("/static/arena.js").text
    assert "syncPair(" in ajs  # arena.js invokes it
