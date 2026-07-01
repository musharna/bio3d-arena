# tests/test_mobile_arena.py
from fastapi.testclient import TestClient
from app.main import app


def test_ab_toggle_markup_present():
    html = TestClient(app).get("/").text
    assert 'class="ab-toggle"' in html
    assert 'data-ab="a"' in html and 'data-ab="b"' in html
    # A is the default-active model column
    assert "model-col is-active" in html
