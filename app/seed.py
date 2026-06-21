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
from .assets_gen import build_asset, build_degenerate
from .database import SessionLocal, init_db
from .models import (
    Category,
    Comparison,
    Criterion,
    Generator,
    GoldPair,
    Metric,
    ModelOutput,
    Rating,
    ReconTask,
    Submission,
    Task,
    Vote,
    VoterSession,
)
from .molec_gen import build_molecule_pdb, build_molecule_sdf
from .storage import get_storage


def _publish(rel: Path) -> None:
    """Push a locally-generated seed asset to remote storage (no-op for local)."""
    storage = get_storage()
    if storage.remote:
        storage.save(str(rel).replace("\\", "/"), (config.ASSET_DIR / rel).read_bytes())


def _seed_reference_demo(db: Session) -> int:
    """Wire the benchmark crambin-fold task as a reference + add perturbed near/far
    generator outputs, so the reference-based validation path has real numbers."""
    from . import validation

    task = db.execute(select(Task).where(Task.title.like("Crambin%"))).scalars().first()
    if task is None:
        return 0
    ref_out = (
        db.execute(select(ModelOutput).where(ModelOutput.task_id == task.id)).scalars().first()
    )
    if ref_out is None:
        return 0
    task.reference_asset_id = ref_out.id
    ref_text = get_storage().read(ref_out.asset_path).decode("utf-8", errors="replace")

    added = 0
    for gslug, gname, sigma in [
        ("predictor-near", "Predictor (near)", 0.3),
        ("predictor-far", "Predictor (far)", 2.5),
    ]:
        gen = db.execute(select(Generator).where(Generator.slug == gslug)).scalars().first()
        if gen is None:
            gen = Generator(slug=gslug, name=gname, kind="model", is_anonymous=True)
            db.add(gen)
            db.flush()
        text = validation.perturb_pdb(ref_text, sigma, seed=abs(hash(gslug)) % 100000)
        rel = Path("seed") / f"crambin__{gslug}.pdb"
        (config.ASSET_DIR / rel).parent.mkdir(parents=True, exist_ok=True)
        (config.ASSET_DIR / rel).write_text(text)
        _publish(rel)
        db.add(
            ModelOutput(
                task_id=task.id,
                generator_id=gen.id,
                title=f"{task.title} — {gname}",
                asset_path=str(rel).replace("\\", "/"),
                asset_format="pdb",
                meta_json=json.dumps({"generator": gslug, "perturb_sigma": sigma}),
            )
        )
        added += 1
    db.flush()
    return added


# The launch recon bake-off: (gt-bundle species slug, display name, prompt). The slug is
# the held-out GT key the scoring service resolves (gt_bundle_prod) — keep these EXACT.
RECON_SPECIES = [
    ("arabidopsis_thaliana", "Arabidopsis thaliana", "thale cress whole-plant rosette"),
    ("solanum_lycopersicum", "Solanum lycopersicum", "tomato whole plant"),
    ("zea_mays", "Zea mays", "maize whole plant"),
    ("pinus_sylvestris", "Pinus sylvestris", "Scots pine sapling"),
]
# The method roster (D5): single-image→3D reconstructors. Expansion toward the Plant
# Methods 2025 six — InstantMesh is the first comprehensive-baselines add beyond the v1 pair.
RECON_GENERATORS = [
    ("trellis", "TRELLIS"),
    ("hunyuan3d", "Hunyuan3D"),
    ("instantmesh", "InstantMesh"),
]


def seed_recon_benchmark(db: Session) -> dict:
    """Idempotent B5-prep: a 'plants' category, the 4 species Tasks each bound to their
    GT-bundle species slug (ReconTask), and the 2 method Generators. After this runs, the
    moment recon GLBs are ingested onto a species Task, /admin/rescore resolves the slug and
    scores against the live service — no further scaffolding needed."""
    cat = db.execute(select(Category).where(Category.slug == "plants")).scalars().first()
    if cat is None:
        cat = Category(slug="plants", name="Plants", description="Whole plants (image→3D recon)")
        db.add(cat)
        db.flush()

    n_tasks = 0
    for slug, sci_name, descr in RECON_SPECIES:
        title = f"{sci_name} — single-image → 3D reconstruction"
        task = (
            db.execute(select(Task).where(Task.title == title, Task.category_id == cat.id))
            .scalars()
            .first()
        )
        if task is None:
            task = Task(
                category_id=cat.id,
                title=title,
                prompt=f"Reconstruct a 3D model of a {descr} from a single RGB image.",
                criteria_note="Scored against held-out GT scans (Mode-B) + perceptual votes (Mode-A).",
            )
            db.add(task)
            db.flush()
        rt = db.execute(select(ReconTask).where(ReconTask.task_id == task.id)).scalars().first()
        if rt is None:
            db.add(ReconTask(task_id=task.id, species_slug=slug, species_name=sci_name))
        else:
            rt.species_slug, rt.species_name = slug, sci_name
        n_tasks += 1

    n_gens = 0
    for gslug, gname in RECON_GENERATORS:
        gen = db.execute(select(Generator).where(Generator.slug == gslug)).scalars().first()
        if gen is None:
            db.add(Generator(slug=gslug, name=gname, kind="model", is_anonymous=True))
        n_gens += 1

    db.flush()
    return {"tasks": n_tasks, "generators": n_gens}


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
# kind: "mesh" → GLB via assets_gen · "pdb" → PDB via molec_gen · "sdf" → SDF via molec_gen
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
    (
        "ligand-sdf",
        "molecules",
        "Small molecule (SDF/molfile)",
        "Generate a small organic molecule as an MDL SDF connection table.",
        "molecule",
        "sdf",
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
            # Delete children before parents to respect FKs.
            for model in (
                Vote,
                Comparison,
                GoldPair,
                Submission,
                Rating,
                Metric,  # child of ModelOutput — delete before it
                VoterSession,
                ModelOutput,
                ReconTask,  # child of Task — delete before it
                Task,
                Generator,
                Criterion,
                Category,
            ):
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
        task_by_slug: dict[str, tuple[Task, str]] = {}
        for tslug, cslug, title, prompt, shape, kind in TASKS:
            t = Task(category_id=cats[cslug].id, title=title, prompt=prompt)
            db.add(t)
            db.flush()
            task_by_slug[tslug] = (t, shape)
            ext = {"pdb": "pdb", "sdf": "sdf"}.get(kind, "glb")
            for gslug, gen in gens.items():
                seed = _seed_int(tslug, gslug)
                rel = Path("seed") / f"{tslug}__{gslug}.{ext}"
                if kind == "pdb":
                    meta = build_molecule_pdb(seed, config.ASSET_DIR / rel)
                elif kind == "sdf":
                    meta = build_molecule_sdf(seed, config.ASSET_DIR / rel)
                else:
                    meta = build_asset(shape, seed, config.ASSET_DIR / rel)
                _publish(rel)
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

        # Gold attention checks: a calibration generator whose outputs (a good
        # asset vs a degenerate one) are is_gold=True, so they never enter normal
        # matchmaking or rankings — used only to score voter trust.
        n_gold = _seed_gold(db, task_by_slug)

        # Register bundled real, openly-licensed benchmark assets (best-effort).
        from .benchmarks import load_benchmarks

        bench_dir = Path(__file__).resolve().parent / "data" / "benchmarks"
        n_bench = {"tasks": 0, "outputs": 0, "skipped": 0}
        if (bench_dir / "manifest.json").exists():
            try:
                n_bench = load_benchmarks(db, bench_dir / "manifest.json", bench_dir)
            except Exception as exc:  # noqa: BLE001 — seeding must not fail on a bad asset
                print(f"benchmark load skipped: {exc}")

        # Reference demo + objective structure validation for every output.
        _seed_reference_demo(db)
        from . import validation_service

        for out in db.execute(select(ModelOutput)).scalars().all():
            validation_service.validate_and_store(db, out)

        # Recon Mode-B benchmark scaffolding (species Tasks + slug mapping + method gens),
        # so ingested recon GLBs are scorable immediately.
        seed_recon_benchmark(db)

        db.commit()
        return {
            "status": "seeded",
            "categories": len(cats),
            "criteria": len(crits),
            "generators": len(gens),
            "tasks": len(TASKS),
            "outputs": n_outputs,
            "gold_pairs": n_gold,
            "benchmarks": n_bench,
        }
    finally:
        if own:
            db.close()


# Tasks to build gold attention-check pairs for (must be mesh tasks).
GOLD_TASKS = ["rose-bloom", "plant-cell"]


def _seed_gold(db: Session, task_by_slug: dict[str, tuple[Task, str]]) -> int:
    """Create a calibration generator + good/decoy gold outputs + GoldPair rows."""
    calib = Generator(
        slug="calibration", name="Calibration (gold)", kind="decoy", is_anonymous=True
    )
    db.add(calib)
    db.flush()

    n = 0
    for tslug in GOLD_TASKS:
        if tslug not in task_by_slug:
            continue
        task, shape = task_by_slug[tslug]
        good_rel = Path("gold") / f"{tslug}__good.glb"
        bad_rel = Path("gold") / f"{tslug}__bad.glb"
        build_asset(shape, _seed_int("gold-good", tslug), config.ASSET_DIR / good_rel)
        build_degenerate(config.ASSET_DIR / bad_rel)
        _publish(good_rel)
        _publish(bad_rel)
        good = ModelOutput(
            task_id=task.id,
            generator_id=calib.id,
            title="gold-good",
            asset_path=str(good_rel).replace("\\", "/"),
            asset_format="glb",
            is_gold=True,
            meta_json=json.dumps({"gold": "good"}),
        )
        bad = ModelOutput(
            task_id=task.id,
            generator_id=calib.id,
            title="gold-bad",
            asset_path=str(bad_rel).replace("\\", "/"),
            asset_format="glb",
            is_gold=True,
            meta_json=json.dumps({"gold": "bad"}),
        )
        db.add_all([good, bad])
        db.flush()
        db.add(
            GoldPair(
                task_id=task.id,
                good_output_id=good.id,
                bad_output_id=bad.id,
                note=f"seeded gold for {tslug}",
            )
        )
        n += 1
    return n


if __name__ == "__main__":
    print(seed_all(force=True))
