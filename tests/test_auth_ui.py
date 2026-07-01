# tests/test_auth_ui.py
from fastapi.testclient import TestClient
from app import config, main


def test_login_link_shown_when_enabled_and_logged_out(monkeypatch):
    monkeypatch.setattr(config, "HF_CLIENT_ID", "cid")
    monkeypatch.setattr(config, "HF_CLIENT_SECRET", "sec")
    r = TestClient(main.app).get("/")
    assert r.status_code == 200 and "/auth/login" in r.text


def test_no_login_ui_when_disabled(monkeypatch):
    monkeypatch.setattr(config, "HF_CLIENT_ID", "")
    monkeypatch.setattr(config, "HF_CLIENT_SECRET", "")
    r = TestClient(main.app).get("/")
    assert r.status_code == 200 and "/auth/login" not in r.text
