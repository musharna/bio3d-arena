import pytest
from app import auth, config


def test_authorize_url_has_params():
    url = auth.authorize_url("st8", "https://x/auth/callback")
    assert url.startswith("https://huggingface.co/oauth/authorize?")
    assert "state=st8" in url and "scope=openid+profile" in url and "response_type=code" in url


def test_exchange_code_returns_token(monkeypatch):
    monkeypatch.setattr(config, "HF_CLIENT_ID", "cid")
    monkeypatch.setattr(config, "HF_CLIENT_SECRET", "sec")
    called = {}

    def fake_post(url, data):
        called["url"] = url
        called["data"] = data
        return {"access_token": "tok-abc"}

    assert auth.exchange_code("code123", "https://x/cb", _post=fake_post) == "tok-abc"
    assert called["url"] == "https://huggingface.co/oauth/token"
    assert called["data"]["code"] == "code123"


def test_exchange_code_raises_on_bad_response(monkeypatch):
    monkeypatch.setattr(config, "HF_CLIENT_ID", "cid")
    monkeypatch.setattr(config, "HF_CLIENT_SECRET", "sec")
    with pytest.raises(auth.AuthError):
        auth.exchange_code("c", "https://x/cb", _post=lambda u, d: {"error": "bad"})


def test_fetch_userinfo_maps_fields():
    info = auth.fetch_userinfo(
        "tok", _get=lambda url, tok: {"sub": "hf-9", "preferred_username": "bob"}
    )
    assert info == {"hf_id": "hf-9", "username": "bob"}


def test_fetch_userinfo_requires_sub():
    with pytest.raises(auth.AuthError):
        auth.fetch_userinfo("tok", _get=lambda url, tok: {"preferred_username": "bob"})
