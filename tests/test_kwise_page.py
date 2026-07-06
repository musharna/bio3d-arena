# tests/test_kwise_page.py
from fastapi.testclient import TestClient
from app.main import app


def test_arena_page_has_kwise_scaffold():
    # NOTE: the arena page is served at "/" (see app.main:index) — the brief's illustrative
    # test used "/arena", which is not a real route here.
    c = TestClient(app)
    r = c.get("/")
    assert r.status_code == 200
    assert "kwise-grid" in r.text  # the 4-up container the JS toggles
    assert "kwise-allbad" in r.text  # the all-bad button
