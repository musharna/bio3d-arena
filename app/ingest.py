"""Programmatic ingestion — register generator outputs (3D assets) into the arena.

The bridge from "demo blobs" to real content: a generator pipeline bakes its
output to a GLB and POSTs it here. Assets are validated (must parse + have
geometry), deduplicated by content hash within a (task, generator), and stored
with free-form provenance in `meta_json`.

Pure helpers over a Session — the FastAPI routes are thin wrappers.
"""

from __future__ import annotations

import hashlib
import io
import json
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config
from .models import Category, Generator, ModelOutput, Task

ALLOWED_FORMATS = {"glb", "gltf"}  # what <model-viewer> can render today


class IngestError(ValueError):
    """Raised for client errors (bad format, unparseable asset, missing task)."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_3d_asset(data: bytes, ext: str) -> dict:
    """Confirm the bytes are a loadable mesh with geometry. Returns provenance stats.

    Raises IngestError on unknown format, parse failure, or empty geometry.
    """
    ext = ext.lower()
    if ext not in ALLOWED_FORMATS:
        raise IngestError(
            f"Unsupported format '{ext}'. Arena renders {sorted(ALLOWED_FORMATS)} (GLB/GLTF)."
        )
    import trimesh  # local import: heavy, not needed for most routes

    try:
        loaded = trimesh.load(io.BytesIO(data), file_type=ext)
    except Exception as exc:  # noqa: BLE001 — surface the real parse error to the client
        raise IngestError(f"Could not parse {ext} asset: {exc}") from exc

    if isinstance(loaded, trimesh.Scene):
        geoms = list(loaded.geometry.values())
        vtx = int(sum(len(g.vertices) for g in geoms))
        n_meshes = len(geoms)
    else:
        vtx = int(len(getattr(loaded, "vertices", [])))
        n_meshes = 1
    if vtx == 0:
        raise IngestError("Asset has no geometry (zero vertices).")
    return {"vertices": vtx, "meshes": n_meshes}


def upsert_category(
    db: Session, slug: str, name: str | None = None, description: str = ""
) -> Category:
    cat = db.execute(select(Category).where(Category.slug == slug)).scalars().first()
    if cat is None:
        cat = Category(
            slug=slug, name=name or slug.replace("-", " ").title(), description=description
        )
        db.add(cat)
        db.flush()
    elif name:
        cat.name = name
    return cat


def upsert_generator(
    db: Session, slug: str, name: str | None = None, kind: str = "model", description: str = ""
) -> Generator:
    gen = db.execute(select(Generator).where(Generator.slug == slug)).scalars().first()
    if gen is None:
        gen = Generator(
            slug=slug, name=name or slug, kind=kind, description=description, is_anonymous=True
        )
        db.add(gen)
        db.flush()
    elif name:
        gen.name = name
    return gen


def create_task(
    db: Session, category_slug: str, title: str, prompt: str, criteria_note: str = ""
) -> Task:
    cat = db.execute(select(Category).where(Category.slug == category_slug)).scalars().first()
    if cat is None:
        raise IngestError(f"Unknown category '{category_slug}'. Create it first.")
    task = Task(category_id=cat.id, title=title, prompt=prompt, criteria_note=criteria_note)
    db.add(task)
    db.flush()
    return task


def register_output(
    db: Session,
    task_id: int,
    generator_slug: str,
    data: bytes,
    ext: str = "glb",
    title: str = "",
    meta: dict | None = None,
    generator_name: str | None = None,
) -> tuple[ModelOutput, bool]:
    """Validate + store a generator's 3D asset for a task. Returns (output, created).

    Idempotent within a (task, generator): re-posting identical bytes returns the
    existing output instead of duplicating it.
    """
    task = db.get(Task, task_id)
    if task is None:
        raise IngestError(f"Unknown task_id {task_id}.")
    gen = upsert_generator(db, generator_slug, name=generator_name)

    stats = validate_3d_asset(data, ext)
    digest = sha256(data)

    # Dedup: same content for the same (task, generator) → return existing.
    existing = (
        db.execute(
            select(ModelOutput).where(
                ModelOutput.task_id == task_id, ModelOutput.generator_id == gen.id
            )
        )
        .scalars()
        .all()
    )
    for out in existing:
        try:
            if json.loads(out.meta_json).get("sha256") == digest:
                return out, False
        except (ValueError, TypeError):
            continue

    rel = Path("uploads") / f"{uuid.uuid4().hex}.{ext.lower()}"
    dest = config.ASSET_DIR / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)

    provenance = {"sha256": digest, "ingested": True, **stats, **(meta or {})}
    output = ModelOutput(
        task_id=task_id,
        generator_id=gen.id,
        title=title or f"{task.title} — {gen.name}",
        asset_path=str(rel).replace("\\", "/"),
        asset_format=ext.lower(),
        meta_json=json.dumps(provenance),
    )
    db.add(output)
    db.flush()
    return output, True
