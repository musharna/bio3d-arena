from fastapi.testclient import TestClient
from app import auth, config, main
from app.database import SessionLocal
from app.models import User, VoterSession
from sqlalchemy import select


def _client():
    return TestClient(main.app)


def test_login_disabled_redirects_home(monkeypatch):
    monkeypatch.setattr(config, "HF_CLIENT_ID", "")
    monkeypatch.setattr(config, "HF_CLIENT_SECRET", "")
    r = _client().get("/auth/login", follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"] == "/"


def test_login_enabled_redirects_to_hf(monkeypatch):
    monkeypatch.setattr(config, "HF_CLIENT_ID", "cid")
    monkeypatch.setattr(config, "HF_CLIENT_SECRET", "sec")
    r = _client().get("/auth/login", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert r.headers["location"].startswith("https://huggingface.co/oauth/authorize?")
    assert "bio3d_oauth_state" in r.headers.get("set-cookie", "")


def test_callback_links_user(monkeypatch):
    monkeypatch.setattr(config, "HF_CLIENT_ID", "cid")
    monkeypatch.setattr(config, "HF_CLIENT_SECRET", "sec")
    monkeypatch.setattr(auth, "exchange_code", lambda code, ru, **k: "tok")
    monkeypatch.setattr(
        auth, "fetch_userinfo", lambda tok, **k: {"hf_id": "hf-77", "username": "carol"}
    )
    c = _client()
    c.get("/")  # establishes the bio3d_session cookie
    c.get("/auth/login", follow_redirects=False)  # sets bio3d_oauth_state cookie
    state = c.cookies.get("bio3d_oauth_state")
    r = c.get(f"/auth/callback?code=abc&state={state}", follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"].startswith("/")
    with SessionLocal() as db:
        u = db.execute(select(User).where(User.hf_id == "hf-77")).scalars().first()
        assert u is not None and u.username == "carol"
        sid = c.cookies.get("bio3d_session")
        vs = db.get(VoterSession, sid)
        assert vs is not None and vs.user_id == u.id


def test_callback_state_mismatch_persists_nothing(monkeypatch):
    monkeypatch.setattr(config, "HF_CLIENT_ID", "cid")
    monkeypatch.setattr(config, "HF_CLIENT_SECRET", "sec")
    c = _client()
    c.get("/")
    c.get("/auth/login", follow_redirects=False)
    r = c.get("/auth/callback?code=abc&state=WRONG", follow_redirects=False)
    assert r.status_code in (302, 307) and "login=error" in r.headers["location"]
