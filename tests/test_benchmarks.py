"""Tests for the benchmark manifest loader."""

from __future__ import annotations

import json

from app import benchmarks
from app.database import SessionLocal
from app.models import ModelOutput, Task
from app.molec_gen import build_molecule_sdf
from app.seed import seed_all


def setup_module(_module):
    seed_all(force=True)  # categories exist


def _fixture(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    build_molecule_sdf(1, assets / "lig.sdf")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            [
                {
                    "task_slug": "bench-lig",
                    "category": "molecules",
                    "title": "Benchmark ligand",
                    "prompt": "A curated benchmark ligand.",
                    "generator_slug": "bench-native",
                    "generator_name": "Benchmark native",
                    "file": "assets/lig.sdf",
                    "format": "sdf",
                    "source": "https://example.org/lig",
                    "license": "CC0",
                    "attribution": "Example",
                }
            ]
        )
    )
    return manifest, assets


def test_load_manifest_parses(tmp_path):
    manifest, _ = _fixture(tmp_path)
    entries = benchmarks.load_manifest(manifest)
    assert entries[0]["task_slug"] == "bench-lig"


def test_load_benchmarks_registers_task_and_output(tmp_path):
    manifest, assets = _fixture(tmp_path)
    with SessionLocal() as db:
        summary = benchmarks.load_benchmarks(db, manifest, assets)
        db.commit()
        assert summary["outputs"] == 1
        out = db.query(ModelOutput).join(Task, ModelOutput.task_id == Task.id).filter(Task.title == "Benchmark ligand").one()
        assert out.asset_format == "sdf"
        meta = json.loads(out.meta_json)
        assert meta["license"] == "CC0"
        assert meta["source"].startswith("http")


def test_load_benchmarks_idempotent(tmp_path):
    manifest, assets = _fixture(tmp_path)
    with SessionLocal() as db:
        benchmarks.load_benchmarks(db, manifest, assets)
        db.commit()
        second = benchmarks.load_benchmarks(db, manifest, assets)
        db.commit()
    assert second["outputs"] == 0  # same bytes → dedup, nothing new
    assert second["skipped"] == 1
