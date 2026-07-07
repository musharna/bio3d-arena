# tests/test_mobile_arena.py
from fastapi.testclient import TestClient
from app.main import app


def test_ab_toggle_markup_present():
    # The arena moved to "/arena" (Task 9 of the design refresh); "/" is now the landing page.
    html = TestClient(app).get("/arena").text
    assert 'class="ab-toggle"' in html
    assert 'data-ab="a"' in html and 'data-ab="b"' in html
    # A is the default-active model column
    assert "model-col is-active" in html
