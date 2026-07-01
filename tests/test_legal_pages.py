from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_legal_pages_serve():
    for path in ("/terms", "/privacy", "/licenses"):
        r = client.get(path)
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
