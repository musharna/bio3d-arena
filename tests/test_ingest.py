"""Tests for the programmatic ingestion API (token, GLB validation, dedup)."""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from app.assets_gen import build_asset
from app.main import app
from app.seed import seed_all

client = TestClient(app)
AUTH = {"X-Admin-Token": "test-token"}


def setup_module(_module):
    seed_all(force=True)


def _baked_glb_bytes(seed: int = 1) -> bytes:
    tmp = Path(tempfile.mkdtemp(prefix="bio3d_ing_")) / "flower.glb"
    build_asset("flower", seed, tmp)
    return tmp.read_bytes()


def test_outputs_requires_token():
    r = client.post(
        "/api/outputs",
        data={"task_id": "1", "generator_slug": "x"},
        files={"file": ("a.glb", _baked_glb_bytes(), "model/gltf-binary")},
    )
    assert r.status_code == 401


def test_outputs_rejects_non_glb_bytes():
    r = client.post(
        "/api/outputs",
        data={"task_id": "1", "generator_slug": "junk-gen"},
        files={"file": ("a.glb", b"not a real glb", "model/gltf-binary")},
        headers=AUTH,
    )
    assert r.status_code == 400


def test_upsert_create_register_and_dedup():
    # Upsert taxonomy + task + generator via JSON.
    assert (
        client.post(
            "/api/categories", json={"slug": "ing-flowers", "name": "Ingest Flowers"}, headers=AUTH
        ).status_code
        == 200
    )
    task = client.post(
        "/api/tasks",
        json={"category": "ing-flowers", "title": "Ingest rose", "prompt": "rose"},
        headers=AUTH,
    ).json()
    assert (
        client.post(
            "/api/generators", json={"slug": "ing-gen", "name": "Ingest Gen"}, headers=AUTH
        ).status_code
        == 200
    )

    glb = _baked_glb_bytes(seed=42)
    first = client.post(
        "/api/outputs",
        data={"task_id": str(task["id"]), "generator_slug": "ing-gen", "meta": '{"seed": 42}'},
        files={"file": ("rose.glb", glb, "model/gltf-binary")},
        headers=AUTH,
    )
    assert first.status_code == 200
    body = first.json()
    assert body["created"] is True
    assert body["meta"]["vertices"] > 0
    assert body["meta"]["seed"] == 42
    assert body["asset_url"].startswith("/assets/")

    # Re-posting identical bytes for the same (task, generator) dedups.
    dup = client.post(
        "/api/outputs",
        data={"task_id": str(task["id"]), "generator_slug": "ing-gen"},
        files={"file": ("rose.glb", glb, "model/gltf-binary")},
        headers=AUTH,
    ).json()
    assert dup["created"] is False
    assert dup["id"] == body["id"]


def test_outputs_unknown_task():
    r = client.post(
        "/api/outputs",
        data={"task_id": "99999", "generator_slug": "ing-gen"},
        files={"file": ("a.glb", _baked_glb_bytes(), "model/gltf-binary")},
        headers=AUTH,
    )
    assert r.status_code == 400


def test_auto_generator_creation_and_task_listing():
    # Registering with a brand-new generator slug auto-creates it.
    task = client.post(
        "/api/tasks",
        json={"category": "ing-flowers", "title": "Ingest tulip", "prompt": "tulip"},
        headers=AUTH,
    ).json()
    client.post(
        "/api/outputs",
        data={
            "task_id": str(task["id"]),
            "generator_slug": "fresh-auto-gen",
            "generator_name": "Fresh Auto Gen",
        },
        files={"file": ("t.glb", _baked_glb_bytes(7), "model/gltf-binary")},
        headers=AUTH,
    ).raise_for_status()
    tasks = {t["id"]: t for t in client.get("/api/tasks").json()["tasks"]}
    assert tasks[task["id"]]["n_outputs"] == 1
