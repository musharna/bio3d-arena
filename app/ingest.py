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

from .models import Category, Generator, ModelOutput, Task
from .storage import get_storage

MESH_FORMATS = {"glb", "gltf"}  # rendered by <model-viewer>
PDB_FORMATS = {"pdb", "cif", "mmcif", "ent"}  # rendered by 3Dmol.js (atomic coords)
SDF_FORMATS = {"sdf", "mol"}  # rendered by 3Dmol.js (connection-table molfiles)
MOLECULAR_FORMATS = PDB_FORMATS | SDF_FORMATS
ALLOWED_FORMATS = MESH_FORMATS | MOLECULAR_FORMATS


class IngestError(ValueError):
    """Raised for client errors (bad format, unparseable asset, missing task)."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_asset(data: bytes, ext: str) -> dict:
    """Validate an asset by format family and return provenance stats.

    Meshes (GLB/GLTF) must load with geometry; PDB/mmCIF must contain atom
    records; SDF/MOL must have a V2000/V3000 counts line with ≥1 atom. Raises
    IngestError on unknown/unparseable/empty assets.
    """
    ext = ext.lower()
    if ext in MESH_FORMATS:
        return _validate_mesh(data, ext)
    if ext in SDF_FORMATS:
        return _validate_sdf(data, ext)
    if ext in PDB_FORMATS:
        return _validate_molecular(data, ext)
    raise IngestError(f"Unsupported format '{ext}'. Arena renders {sorted(ALLOWED_FORMATS)}.")


# Backwards-compatible alias.
validate_3d_asset = validate_asset


def _validate_mesh(data: bytes, ext: str) -> dict:
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
    return {"kind": "mesh", "vertices": vtx, "meshes": n_meshes}


def _validate_molecular(data: bytes, ext: str) -> dict:
    try:
        text = data.decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        raise IngestError(f"Could not decode {ext} text: {exc}") from exc

    if ext in ("pdb", "ent"):
        atoms = sum(1 for line in text.splitlines() if line.startswith(("ATOM", "HETATM")))
        if atoms == 0:
            raise IngestError("PDB has no ATOM/HETATM records.")
        return {"kind": "molecular", "atoms": atoms}

    # mmCIF / CIF
    if "_atom_site" not in text:
        raise IngestError("mmCIF/CIF missing the _atom_site loop (no atoms).")
    atoms = sum(1 for line in text.splitlines() if line.startswith(("ATOM ", "HETATM")))
    return {"kind": "molecular", "atoms": atoms}


def _validate_sdf(data: bytes, ext: str) -> dict:
    """Validate an MDL MOL/SDF connection-table file (V2000 or V3000)."""
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    # MOL/SDF layout: 3 header lines, then the counts line.
    if len(lines) < 4:
        raise IngestError("SDF/MOL too short — missing the counts line.")
    counts = lines[3]
    if "V2000" not in counts and "V3000" not in counts:
        raise IngestError("SDF/MOL counts line missing V2000/V3000 tag.")
    n_atoms = 0
    if "V3000" in counts:
        for ln in lines:
            if ln.strip().startswith("M  V30 COUNTS"):
                try:
                    n_atoms = int(ln.split()[3])
                except (IndexError, ValueError):
                    n_atoms = 0
                break
    else:  # V2000 packs atom count in the first 3 columns of the counts line
        try:
            n_atoms = int(counts[:3])
        except ValueError:
            n_atoms = 0
    if n_atoms <= 0:
        raise IngestError("SDF/MOL has zero atoms.")
    n_mols = text.count("$$$$") or 1
    return {"kind": "molecular", "atoms": n_atoms, "molecules": n_mols, "subformat": "sdf"}


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

    rel = str(Path("uploads") / f"{uuid.uuid4().hex}.{ext.lower()}").replace("\\", "/")
    get_storage().save(rel, data)

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
    # Best-effort structural admissibility verdict so new assets are gated from first appearance.
    # Guarded: a bad asset records a reject, never breaks ingest.
    try:
        from . import structural

        structural.evaluate_outputs(db, [output.id])
    except Exception:  # noqa: BLE001 — ingest must not fail on the admissibility hook
        pass
    return output, True
