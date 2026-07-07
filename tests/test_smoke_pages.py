import pytest
from starlette.testclient import TestClient
from app.main import app

PUBLIC_PATHS = [
    "/",
    "/leaderboard",
    "/significance",
    "/benchmark",
    "/dataset",
    "/difficulty",
    "/tasks",
    "/submit",
    "/coverage",
    "/procedural",
    "/fidelity",
    "/methodology",
    "/terms",
    "/privacy",
    "/licenses",
    "/spotlight",
    "/methodology",
]


@pytest.fixture
def client():
    return TestClient(app)


@pytest.mark.parametrize("path", PUBLIC_PATHS)
def test_public_page_200(client, path):
    r = client.get(path)
    assert r.status_code == 200, f"{path} -> {r.status_code}"
    assert b"<html" in r.content.lower()
