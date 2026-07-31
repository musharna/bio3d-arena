# tests/test_flag_client.py
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.seed import seed_all

client = TestClient(app)


def setup_module(_m):
    seed_all(force=True)


def test_next_payload_has_output_id_and_no_generator_leak():
    data = client.get("/api/next?set=pair").json()
    assert "output_id" in data["a"] and "output_id" in data["b"]
    assert isinstance(data["a"]["output_id"], int)
    assert "generator" not in str(data).lower()  # still anonymized


def test_viewer_and_arena_wire_the_flag_button():
    vjs = client.get("/static/viewer.js").text
    assert "onFlag" in vjs  # control hook exists
    ajs = client.get("/static/arena.js").text
    assert "/api/flag" in ajs and "output_id" in ajs
