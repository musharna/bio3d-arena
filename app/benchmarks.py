"""Curated benchmark loader — register real, openly-licensed 3D assets as tasks.

A manifest (JSON list of entries) maps a curated asset file to a (category, task,
generator). Each entry records source/license/attribution provenance, stored in
model_output.meta_json. Idempotent via the content-hash dedup in register_output.
"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import ingest
from .models import Task

REQUIRED_FIELDS = ("task_slug", "category", "title", "prompt", "generator_slug", "file", "format")


def load_manifest(path: Path) -> list[dict]:
    """Parse + lightly validate a benchmark manifest. Raises on malformed entries."""
    entries = json.loads(Path(path).read_text())
    if not isinstance(entries, list):
        raise ingest.IngestError("Manifest must be a JSON list of entries.")
    for i, e in enumerate(entries):
        missing = [f for f in REQUIRED_FIELDS if f not in e]
        if missing:
            raise ingest.IngestError(f"Manifest entry {i} missing fields: {missing}")
    return entries


def _task_for_slug(db: Session, entry: dict) -> Task:
    """Find-or-create the task for an entry, keyed by title (the grouping key by design)."""
    existing = db.execute(select(Task).where(Task.title == entry["title"])).scalars().first()
    if existing is not None:
        return existing
    ingest.upsert_category(db, entry["category"])
    return ingest.create_task(
        db,
        category_slug=entry["category"],
        title=entry["title"],
        prompt=entry["prompt"],
        criteria_note=entry.get("criteria_note", ""),
    )


def register_benchmark_entry(db: Session, entry: dict, assets_dir: Path) -> tuple[int, bool]:
    """Register one manifest entry's asset as a ModelOutput. Returns (output_id, created)."""
    data = (Path(assets_dir) / entry["file"]).read_bytes()
    task = _task_for_slug(db, entry)
    meta = {
        "benchmark": True,
        "task_slug": entry["task_slug"],
        "source": entry.get("source", ""),
        "license": entry.get("license", ""),
        "attribution": entry.get("attribution", ""),
    }
    output, created = ingest.register_output(
        db,
        task_id=task.id,
        generator_slug=entry["generator_slug"],
        data=data,
        ext=entry["format"],
        title=entry.get("output_title", ""),
        meta=meta,
        generator_name=entry.get("generator_name"),
    )
    return output.id, created


def load_benchmarks(db: Session, manifest_path: Path, assets_dir: Path) -> dict:
    """Register every entry in a manifest. Idempotent (content-hash dedup).

    entry["file"] paths are resolved relative to assets_dir.
    Multiple entries sharing the same title map to the same Task (by design).
    """
    manifest_path = Path(manifest_path)
    assets_dir = Path(assets_dir)
    entries = load_manifest(manifest_path)
    tasks: set[str] = set()
    outputs = skipped = 0
    for entry in entries:
        _, created = register_benchmark_entry(db, entry, assets_dir)
        tasks.add(entry["title"])
        if created:
            outputs += 1
        else:
            skipped += 1
    db.flush()
    return {"tasks": len(tasks), "outputs": outputs, "skipped": skipped}
