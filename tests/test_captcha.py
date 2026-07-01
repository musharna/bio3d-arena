# tests/test_captcha.py
from app import config, integrity


def test_captcha_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(config, "REQUIRE_CAPTCHA", False)
    assert integrity.verify_captcha(None) is True


def test_captcha_calls_provider_when_enabled(monkeypatch):
    monkeypatch.setattr(config, "REQUIRE_CAPTCHA", True)
    monkeypatch.setattr(config, "CAPTCHA_SECRET", "sek")
    monkeypatch.setattr(config, "CAPTCHA_PROVIDER", "turnstile")
    calls = {}

    def fake_post(url, data):
        calls["url"] = url
        calls["data"] = data
        return {"success": True}

    assert integrity.verify_captcha("tok", _post=fake_post) is True
    assert "challenges.cloudflare.com" in calls["url"]
    assert calls["data"]["response"] == "tok"


def test_captcha_rejects_missing_token_when_enabled(monkeypatch):
    monkeypatch.setattr(config, "REQUIRE_CAPTCHA", True)
    monkeypatch.setattr(config, "CAPTCHA_SECRET", "sek")
    assert integrity.verify_captcha(None) is False
def test_captcha_fails_closed_on_non_dict_provider_response(monkeypatch):
    """FIX 3: a syntactically-valid but non-dict provider response (null, a list) must
    fail closed (False), not raise AttributeError out of verify_captcha (which would
    surface as a 500 instead of a clean 403 at the call site)."""
    monkeypatch.setattr(config, "REQUIRE_CAPTCHA", True)
    monkeypatch.setattr(config, "CAPTCHA_SECRET", "sek")
    assert integrity.verify_captcha("tok", _post=lambda url, data: None) is False
    assert integrity.verify_captcha("tok", _post=lambda url, data: []) is False
