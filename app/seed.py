"""Seed the database with demo categories, criteria, generators, tasks, and
procedurally-generated 3D outputs so the arena works end-to-end on first run.

Idempotent: re-running is a no-op unless `force=True` (which wipes + reseeds).
"""

from __future__ import annotations

import json
import zlib
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config
from .assets_gen import build_asset
from .database import SessionLocal, init_db
from .models import Category, Criterion, Generator, ModelOutput, Rating, Task
from .molec_gen import build_molecule_pdb

# (slug, name, description)
CATEGORIES = [
    ("cells", "Cells", "Single cells and organelles"),
    ("flowers", "Flowers", "Flowers and inflorescences"),
    ("roots", "Roots", "Root systems and architecture"),
    ("proteins", "Proteins", "Protein structures and folds"),
    ("molecules", "Molecules", "Small molecules and ligands"),
]

CRITERIA = [
    ("overall", "Overall", "Best output overall, all things considered"),
    ("realism", "Biological realism", "How biologically plausible the structure looks"),
    ("morphology", "Morphology", "Correctness of shape and form"),
    ("structural_accuracy", "Structural accuracy", "Geometric/topological correctness"),
    ("visual_quality", "Visual quality", "Mesh quality, cleanliness, rendering"),
    ("scientific_usefulness", "Scientific usefulness", "Useful for downstream science"),
]

# (slug, name, kind)
GENERATORS = [
    ("gen-alpha", "Generator Alpha", "model"),
    ("gen-beta", "Generator Beta", "model"),
    ("gen-gamma", "Generator Gamma", "model"),
    ("gen-delta", "Generator Delta", "model"),
    ("baseline-blob", "Baseline (blob)", "baseline"),
]

# (slug, category_slug, title, prompt, shape, kind)
# kind: "mesh" → GLB via assets_gen · "pdb" → PDB via molec_gen (3Dmol viewer)
TASKS = [
    (
        "plant-cell",
        "cells",
        "Plant cell with organelles",
        "Generate a 3D model of a plant cell showing the membrane and a nucleus.",
        "cell",
        "mesh",
    ),
    (
        "rose-bloom",
        "flowers",
        "Rose flower in bloom",
        "Generate a 3D model of an open rose flower with layered petals.",
        "flower",
        "mesh",
    ),
    (
        "wheat-root",
        "roots",
        "Wheat seedling root system",
        "Generate a 3D model of a branching wheat root system.",
        "root",
        "mesh",
    ),
    (
        "protein-fold",
        "proteins",
        "Small protein backbone fold",
        "Generate a 3D model of a small protein backbone (~12 residues).",
        "protein",
        "mesh",
    ),
    (
        "ligand",
        "molecules",
        "Small-molecule ligand",
        "Generate a 3D ball-and-stick model of a small organic molecule.",
        "molecule",
        "mesh",
    ),
    (
        "ligand-pdb",
        "molecules",
        "Small molecule (PDB structure)",
        "Generate an atomic-resolution small molecule as a PDB structure.",
        "molecule",
        "pdb",
    ),
]


def _seed_int(*parts: str) -> int:
    """Deterministic 31-bit seed from string parts (avoids Python hash randomization)."""
    return zlib.crc32("|".join(parts).encode()) & 0x7FFFFFFF


def seed_all(db: Session | None = None, force: bool = False) -> dict:
    """Create demo data + assets. Returns a small summary dict."""
    init_db()
    own = db is None
    db = db or SessionLocal()
    try:
        existing = db.execute(select(Category)).scalars().first()
        if existing and not force:
            return {"status": "already-seeded"}
        if force:
            for model in (Rating, ModelOutput, Task, Generator, Criterion, Category):
                db.query(model).delete()
            db.commit()

        cats = {}
        for slug, name, desc in CATEGORIES:
            c = Category(slug=slug, name=name, description=desc)
            db.add(c)
            cats[slug] = c

        crits = {}
        for slug, name, desc in CRITERIA:
            cr = Criterion(slug=slug, name=name, description=desc)
            db.add(cr)
            crits[slug] = cr

        gens = {}
        for slug, name, kind in GENERATORS:
            g = Generator(slug=slug, name=name, kind=kind, is_anonymous=True)
            db.add(g)
            gens[slug] = g
        db.flush()  # assign ids

        n_outputs = 0
        for tslug, cslug, title, prompt, shape, kind in TASKS:
            t = Task(category_id=cats[cslug].id, title=title, prompt=prompt)
            db.add(t)
            db.flush()
            ext = "pdb" if kind == "pdb" else "glb"
            for gslug, gen in gens.items():
                seed = _seed_int(tslug, gslug)
                rel = Path("seed") / f"{tslug}__{gslug}.{ext}"
                if kind == "pdb":
                    meta = build_molecule_pdb(seed, config.ASSET_DIR / rel)
                else:
                    meta = build_asset(shape, seed, config.ASSET_DIR / rel)
                meta["generator"] = gslug
                db.add(
                    ModelOutput(
                        task_id=t.id,
                        generator_id=gen.id,
                        title=f"{title} — {gen.name}",
                        asset_path=str(rel).replace("\\", "/"),
                        asset_format=ext,
                        meta_json=json.dumps(meta),
                    )
                )
                n_outputs += 1

        # Initialize global 'overall' ratings so the leaderboard lists everyone.
        overall = crits["overall"]
        for gen in gens.values():
            db.add(Rating(generator_id=gen.id, category_id=None, criterion_id=overall.id))

        db.commit()
        return {
            "status": "seeded",
            "categories": len(cats),
            "criteria": len(crits),
            "generators": len(gens),
            "tasks": len(TASKS),
            "outputs": n_outputs,
        }
    finally:
        if own:
            db.close()


if __name__ == "__main__":
    print(seed_all(force=True))
