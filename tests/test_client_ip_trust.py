"""Per-IP rate limiting must key on an IP the client cannot choose.

`_client_ip` took `x-forwarded-for.split(",")[0]` whenever TRUST_FORWARDED_FOR was set, and
`fly.toml` sets it. That is safe only if the proxy in front REPLACES a client-supplied header.
Cloudflare does not -- its own documentation says:

    "Cloudflare will append the IP address of the HTTP proxy connecting to Cloudflare to
     the header."

So once the site sits behind Cloudflare, a request carrying `X-Forwarded-For: 1.2.3.4` arrives at
the origin as `1.2.3.4, <real client>`, and taking element [0] hands the rate limiter a value the
attacker picked. A vote farmer rotates that string and never meets the per-IP cap -- defeating
precisely the anti-farming layer it exists to be.

The fix is to prefer headers the edge always sets and a client cannot forge, and to trust each one
ONLY when configured to be behind that edge:

    CF-Connecting-IP   (only when BEHIND_CLOUDFLARE)  -- set by Cloudflare, overwritten every time
    Fly-Client-IP      (only when TRUST_FLY_CLIENT_IP) -- "always set by the Fly Proxy"
    X-Forwarded-For    (only when TRUST_FORWARDED_FOR) -- last resort, and see below
    socket peer

Gating matters as much as ordering: an ungated CF-Connecting-IP would be worse than the bug it
replaces, because with no Cloudflare in front NOTHING strips that header and any client could send
it.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import config
from app.main import _client_ip

REAL = "203.0.113.7"  # what the edge observed
SPOOF = "198.51.100.99"  # what the attacker typed


def req(headers: dict[str, str], peer: str = "10.0.0.1"):
    lower = {k.lower(): v for k, v in headers.items()}
    return SimpleNamespace(
        headers=SimpleNamespace(get=lambda k, d="": lower.get(k.lower(), d)),
        client=SimpleNamespace(host=peer),
    )


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    """Every flag off unless a test turns it on, so nothing leaks between cases."""
    monkeypatch.setattr(config, "TRUST_FORWARDED_FOR", False, raising=False)
    monkeypatch.setattr(config, "BEHIND_CLOUDFLARE", False, raising=False)
    monkeypatch.setattr(config, "TRUST_FLY_CLIENT_IP", False, raising=False)


def test_untrusted_headers_are_ignored():
    """Nothing configured: the socket peer is the only thing that cannot be forged."""
    assert _client_ip(req({"X-Forwarded-For": SPOOF, "CF-Connecting-IP": SPOOF})) == "10.0.0.1"


def test_cf_connecting_ip_wins_over_a_spoofed_forwarded_for(monkeypatch):
    """THE BUG. Behind Cloudflare the attacker's X-Forwarded-For is preserved and appended to,
    so element [0] is their choice; CF-Connecting-IP is Cloudflare's and must win."""
    monkeypatch.setattr(config, "BEHIND_CLOUDFLARE", True)
    monkeypatch.setattr(config, "TRUST_FORWARDED_FOR", True)
    r = req({"X-Forwarded-For": f"{SPOOF}, {REAL}", "CF-Connecting-IP": REAL})
    assert _client_ip(r) == REAL, "rate limiter keyed on an attacker-supplied value"


def test_cf_header_is_not_trusted_when_not_behind_cloudflare(monkeypatch):
    """Positive control for the gate. With no Cloudflare in front nothing strips this header, so
    trusting it unconditionally would hand every client a free spoof."""
    monkeypatch.setattr(config, "TRUST_FORWARDED_FOR", True)
    r = req({"X-Forwarded-For": REAL, "CF-Connecting-IP": SPOOF})
    assert _client_ip(r) == REAL


def test_fly_client_ip_is_preferred_over_forwarded_for(monkeypatch):
    """Fly documents Fly-Client-IP as 'always set by the Fly Proxy'; X-Forwarded-For carries
    whatever the client sent plus whatever the proxy added."""
    monkeypatch.setattr(config, "TRUST_FLY_CLIENT_IP", True)
    monkeypatch.setattr(config, "TRUST_FORWARDED_FOR", True)
    r = req({"X-Forwarded-For": f"{SPOOF}, {REAL}", "Fly-Client-IP": REAL})
    assert _client_ip(r) == REAL


def test_cloudflare_beats_fly_when_both_are_present(monkeypatch):
    """Chained CF -> Fly: Fly-Client-IP is Cloudflare's EDGE address, identical for every visitor.
    Keying on it would put the whole world in one bucket."""
    monkeypatch.setattr(config, "BEHIND_CLOUDFLARE", True)
    monkeypatch.setattr(config, "TRUST_FLY_CLIENT_IP", True)
    r = req({"CF-Connecting-IP": REAL, "Fly-Client-IP": "172.70.0.1"})
    assert _client_ip(r) == REAL


def test_forwarded_for_still_works_alone(monkeypatch):
    """Don't regress the existing deployment: with only TRUST_FORWARDED_FOR set, behaviour is
    unchanged."""
    monkeypatch.setattr(config, "TRUST_FORWARDED_FOR", True)
    assert _client_ip(req({"X-Forwarded-For": f"{REAL}, 10.9.9.9"})) == REAL


def test_blank_and_missing_headers_fall_through(monkeypatch):
    monkeypatch.setattr(config, "BEHIND_CLOUDFLARE", True)
    monkeypatch.setattr(config, "TRUST_FORWARDED_FOR", True)
    assert _client_ip(req({"CF-Connecting-IP": "  ", "X-Forwarded-For": REAL})) == REAL
    assert _client_ip(req({})) == "10.0.0.1"


def test_no_client_at_all_is_not_a_crash():
    r = SimpleNamespace(headers=SimpleNamespace(get=lambda k, d="": d), client=None)
    assert _client_ip(r) == "unknown"


def test_analytics_beacon_is_absent_until_configured(monkeypatch):
    """An instance with no Cloudflare account must render NO beacon — not a request to an
    analytics property that does not exist. This is why the token is config, not a constant."""
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr(config, "CF_ANALYTICS_TOKEN", "", raising=False)
    body = TestClient(app).get("/privacy").text
    assert "cloudflareinsights" not in body

    monkeypatch.setattr(config, "CF_ANALYTICS_TOKEN", "tok-abc123", raising=False)
    body = TestClient(app).get("/privacy").text
    assert "cloudflareinsights.com/beacon.min.js" in body
    assert "tok-abc123" in body
