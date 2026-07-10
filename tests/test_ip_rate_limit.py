"""Per-IP rate limiting closes the cookie-reset farming hole: session-keyed limits reset when a
voter clears their cookie, but a per-IP limiter caps throughput regardless of session. IP is
resolved X-Forwarded-For-aware but only trusts the header behind a known proxy."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app import config, integrity, main
from app.main import app


class _Req:
    """Minimal stand-in for a Starlette Request for _client_ip."""

    def __init__(self, peer: str, xff: str = ""):
        self.headers = {"x-forwarded-for": xff} if xff else {}
        self.client = type("C", (), {"host": peer})()


def test_client_ip_uses_socket_peer_by_default(monkeypatch):
    monkeypatch.setattr(config, "TRUST_FORWARDED_FOR", False)
    # An untrusted client can spoof X-Forwarded-For, so it must be ignored unless behind a proxy.
    assert main._client_ip(_Req("1.2.3.4", "9.9.9.9, 8.8.8.8")) == "1.2.3.4"


def test_client_ip_uses_forwarded_leftmost_when_trusted(monkeypatch):
    monkeypatch.setattr(config, "TRUST_FORWARDED_FOR", True)
    assert main._client_ip(_Req("1.2.3.4", "9.9.9.9, 8.8.8.8")) == "9.9.9.9"


def test_check_ip_rate_limit_namespaced_and_capped(monkeypatch):
    monkeypatch.setattr(config, "IP_VOTE_RATE_LIMIT", 2)
    integrity.reset_rate_limits()
    assert integrity.check_ip_rate_limit("5.5.5.5") is True
    assert integrity.check_ip_rate_limit("5.5.5.5") is True
    assert integrity.check_ip_rate_limit("5.5.5.5") is False  # 3rd exceeds limit 2
    assert integrity.check_ip_rate_limit("6.6.6.6") is True  # a different IP is unaffected
    # IP checks live in their own namespace — they must not consume the session bucket.
    assert integrity.check_rate_limit("5.5.5.5") is True


def test_vote_ip_limit_blocks_cookie_reset(monkeypatch):
    """The money test: exhaust the IP limit, then a request with a FRESH cookie (new session)
    still 429s — clearing cookies no longer resets the cap."""
    monkeypatch.setattr(config, "IP_VOTE_RATE_LIMIT", 3)
    monkeypatch.setattr(config, "TRUST_FORWARDED_FOR", False)
    integrity.reset_rate_limits()
    codes = []
    for _ in range(5):
        fresh = TestClient(app)  # new cookie jar → new session, but same client IP
        r = fresh.post("/api/vote", json={"comparison_id": 99999999, "winner": "a"})
        codes.append(r.status_code)
    # First 3 pass the IP gate (then 404 on the bogus comparison); 4th+ are 429 on the IP cap.
    assert codes[:3] == [404, 404, 404], codes
    assert codes[3] == 429 and codes[4] == 429, codes
