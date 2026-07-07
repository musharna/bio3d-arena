# tests/test_kwise_page.py
from fastapi.testclient import TestClient
from app.main import app


def test_arena_page_has_kwise_scaffold():
    # The arena moved to "/arena" (Task 9 of the design refresh); "/" is now the landing page.
    c = TestClient(app)
    r = c.get("/arena")
    assert r.status_code == 200
    assert "kwise-grid" in r.text  # the 4-up container the JS toggles
    assert "kwise-allbad" in r.text  # the all-bad button
