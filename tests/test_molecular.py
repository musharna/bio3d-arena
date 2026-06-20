"""Tests for molecular-format support (PDB validation, ingest, arena serving)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import ingest
from app.main import app
from app.molec_gen import build_molecule_pdb
from app.seed import seed_all

client = TestClient(app)
AUTH = {"X-Admin-Token": "test-token"}


def setup_module(_module):
    seed_all(force=True)


def _pdb_bytes(seed: int = 3) -> bytes:
    p = Path(tempfile.mkdtemp(prefix="bio3d_pdb_")) / "m.pdb"
    build_molecule_pdb(seed, p)
    return p.read_bytes()


def test_validate_pdb_counts_atoms():
    stats = ingest.validate_asset(_pdb_bytes(), "pdb")
    assert stats["kind"] == "molecular"
    assert stats["atoms"] > 0


def test_validate_rejects_empty_pdb():
    with pytest.raises(ingest.IngestError):
        ingest.validate_asset(b"REMARK nothing here\nEND\n", "pdb")


def test_ingest_pdb_output():
    task = client.post(
        "/api/tasks",
        json={"category": "molecules", "title": "Ingested PDB ligand", "prompt": "x"},
        headers=AUTH,
    ).json()
    r = client.post(
        "/api/outputs",
        data={"task_id": str(task["id"]), "generator_slug": "pdb-gen"},
        files={"file": ("lig.pdb", _pdb_bytes(9), "chemical/x-pdb")},
        headers=AUTH,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["format"] == "pdb"
    assert body["meta"]["kind"] == "molecular"
    assert body["meta"]["atoms"] > 0


def test_arena_serves_pdb_format_and_asset():
    # The seeded molecules category has both a GLB and a PDB task; sample until
    # we observe a PDB-format asset, then confirm the file is served and parses.
    seen_formats = set()
    pdb_url = None
    for _ in range(60):
        data = client.get("/api/next?category=molecules").json()
        for side in ("a", "b"):
            fmt = data[side]["format"]
            seen_formats.add(fmt)
            if fmt == "pdb":
                pdb_url = data[side]["url"]
        if pdb_url:
            break
    assert "pdb" in seen_formats, f"never saw a PDB asset; got {seen_formats}"
    served = client.get(pdb_url)
    assert served.status_code == 200
    assert "HETATM" in served.text or "ATOM" in served.text
