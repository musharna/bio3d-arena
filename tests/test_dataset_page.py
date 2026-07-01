from fastapi.testclient import TestClient
from app import config
from app.main import app


def test_dataset_page_no_release(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "RELEASES_DIR", tmp_path / "releases")
    r = TestClient(app).get("/dataset")
    assert r.status_code == 200
    assert "no release" in r.text.lower()


def test_dataset_page_lists_release(monkeypatch, tmp_path):
    rel = tmp_path / "releases" / "2026.07-v1"
    rel.mkdir(parents=True)
    (rel / "VERSION").write_text("2026.07-v1\nsha256:abc\n")
    (rel / "DATASHEET.md").write_text("# Datasheet 2026.07-v1\n")
    monkeypatch.setattr(config, "RELEASES_DIR", tmp_path / "releases")
    r = TestClient(app).get("/dataset")
    assert r.status_code == 200
    assert "2026.07-v1" in r.text
