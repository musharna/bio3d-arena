"""Tests for the community submission + moderation queue."""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from app.assets_gen import build_asset
from app.database import SessionLocal
from app.main import app
from app.models import ModelOutput, Submission
from app.seed import seed_all

client = TestClient(app)
AUTH = {"X-Admin-Token": "test-token"}


def setup_module(_module):
    seed_all(force=True)


def _glb(seed: int = 1) -> bytes:
    p = Path(tempfile.mkdtemp(prefix="bio3d_sub_")) / "x.glb"
    build_asset("flower", seed, p)
    return p.read_bytes()


def _submit(generator_slug="community-gen", seed=1, task_id=1, fname="x.glb", data=None):
    return client.post(
        "/api/submit",
        data={"task_id": str(task_id), "generator_slug": generator_slug, "title": "Community rose"},
        files={"file": (fname, _glb(seed) if data is None else data, "model/gltf-binary")},
    )


def test_submit_creates_pending():
    r = _submit()
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "pending"
    with SessionLocal() as db:
        sub = db.get(Submission, body["submission_id"])
        assert sub.status == "pending"
        assert sub.model_output_id is None


def test_submit_rejects_bad_asset():
    r = _submit(data=b"not a glb at all")
    assert r.status_code == 400


def test_approve_creates_output_and_enters_arena():
    sub_id = _submit(generator_slug="approve-gen", seed=5).json()["submission_id"]

    # Wrong token rejected.
    assert (
        client.post(
            f"/admin/submissions/{sub_id}/approve", data={"token": "nope"}, follow_redirects=False
        ).status_code
        == 401
    )

    ok = client.post(
        f"/admin/submissions/{sub_id}/approve",
        data={"token": "test-token"},
        follow_redirects=False,
    )
    assert ok.status_code == 303

    with SessionLocal() as db:
        sub = db.get(Submission, sub_id)
        assert sub.status == "approved"
        assert sub.model_output_id is not None
        out = db.get(ModelOutput, sub.model_output_id)
        assert out is not None
        assert out.asset_path == sub.asset_path  # reuses the stored blob
        assert out.is_gold is False  # enters real matchmaking


def test_reject_with_note():
    sub_id = _submit(generator_slug="reject-gen", seed=9).json()["submission_id"]
    r = client.post(
        f"/admin/submissions/{sub_id}/reject",
        data={"token": "test-token", "note": "low quality"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    with SessionLocal() as db:
        sub = db.get(Submission, sub_id)
        assert sub.status == "rejected"
        assert sub.review_note == "low quality"
        assert sub.model_output_id is None


def test_double_moderation_rejected():
    sub_id = _submit(generator_slug="once-gen", seed=3).json()["submission_id"]
    client.post(
        f"/admin/submissions/{sub_id}/approve", data={"token": "test-token"}, follow_redirects=False
    )
    # Approving again should fail (already approved).
    again = client.post(
        f"/admin/submissions/{sub_id}/reject", data={"token": "test-token"}, follow_redirects=False
    )
    assert again.status_code == 400


def test_api_submissions_listing_requires_token():
    assert client.get("/api/submissions").status_code == 401
    listed = client.get("/api/submissions", headers=AUTH)
    assert listed.status_code == 200
    assert isinstance(listed.json()["submissions"], list)


def test_submit_and_moderation_pages_render():
    assert "Submit a model output" in client.get("/submit").text
    # Moderation page is token-gated (renders submitter PII + un-vetted asset URLs).
    assert client.get("/admin/moderation").status_code == 401
    assert client.get("/admin/moderation", params={"token": "test-token"}).status_code == 200
