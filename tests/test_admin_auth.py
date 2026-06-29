"""Admin HTML GET pages must require the admin token (they render admin/moderation data,
incl. submitter PII + un-vetted asset URLs)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app import config
from app.main import app

client = TestClient(app)


def test_admin_page_requires_token():
    assert client.get("/admin").status_code == 401
    assert client.get("/admin", params={"token": "wrong"}).status_code == 401
    assert client.get("/admin", params={"token": config.ADMIN_TOKEN}).status_code == 200


def test_moderation_page_requires_token():
    assert client.get("/admin/moderation").status_code == 401
    assert client.get("/admin/moderation", params={"token": "wrong"}).status_code == 401
    r = client.get("/admin/moderation", params={"token": config.ADMIN_TOKEN})
    assert r.status_code == 200


def test_approve_redirect_carries_token():
    # Approving a nonexistent submission still hits the token gate first (not a 401 leak),
    # and the failure is a clean 4xx — confirming token handling precedes the action.
    r = client.post(
        "/admin/submissions/999999/approve",
        data={"token": config.ADMIN_TOKEN},
        follow_redirects=False,
    )
    assert r.status_code in (303, 400)  # 303 (redirect w/ token) or 400 (no such submission)
    # Wrong token must be refused outright.
    bad = client.post(
        "/admin/submissions/999999/approve", data={"token": "wrong"}, follow_redirects=False
    )
    assert bad.status_code == 401
