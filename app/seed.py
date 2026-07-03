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
    Admissibility,
    CalibrationPair,
    Category,
    Comparison,
    Completeness,
    CommissionAttempt,
    Criterion,
    Critique,
    DGenIteration,
    Generator,
    GoldPair,
    JudgeVote,
    Metric,
    ModelOutput,
    ModelScope,
    OrganMetric,
    OutputFlag,
    Rating,
    ReconTask,
    Submission,
    Task,
    TraitScore,
    TraitVerdict,
    Vote,
    VoterSession,
)
from .storage import get_storage

# All models that must be wiped, in child-before-parent order, when seed_all(force=True)
# does a full reseed. Every model with a ForeignKey("model_output.id") MUST be listed here
# (before ModelOutput) or a force reseed orphans it: SQLite reuses the deleted ModelOutput
# rowids, so the next insert into an orphaned unique-FK child throws UNIQUE constraint
# failed. See tests/test_seed_force_cascade.py, which asserts this list stays in sync with
# the schema.
_FORCE_DELETE_MODELS = (
    Vote,
    Comparison,
    GoldPair,
    Submission,
    Rating,
    Metric,  # child of ModelOutput — delete before it
    Completeness,  # child of ModelOutput — delete before it
    OutputFlag,  # child of ModelOutput — delete before it
    Critique,  # child of ModelOutput — delete before it
    OrganMetric,  # child of ModelOutput — delete before it
    TraitScore,  # child of ModelOutput — delete before it
    ModelScope,  # child of ModelOutput — delete before it
    TraitVerdict,  # child of ModelOutput — delete before it
    DGenIteration,  # child of ModelOutput — delete before it
    JudgeVote,  # child of ModelOutput — delete before it
    CalibrationPair,  # child of ModelOutput — delete before it
    CommissionAttempt,  # child of ModelOutput — delete before it
    Admissibility,  # child of ModelOutput — delete before it
    VoterSession,
    ModelOutput,
    ReconTask,  # child of Task — delete before it
    Task,
    Generator,
    Criterion,
    Category,
)


def _publish(rel: Path) -> None:
    """Push a locally-generated seed asset to remote storage (no-op for local)."""
    storage = get_storage()
    if storage.remote:
        storage.save(str(rel).replace("\\", "/"), (config.ASSET_DIR / rel).read_bytes())


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


# Synthetic-plant fidelity launch types — reuse the recon binomial slugs so the existing
# bake-off GLBs populate these tasks day-one (cross-paradigm: procedural vs reconstructed).
SYNTH_TYPES = [(slug, sci_name) for slug, sci_name, _descr in RECON_SPECIES]


def _synth_title(sci_name: str) -> str:
    return f"{sci_name} — botanical plausibility"


def synth_task_for_slug(db: Session, slug: str):
    """Resolve a binomial species slug to its synthetic-plants Task (by title), or None."""
    by_slug = dict(SYNTH_TYPES)
    sci = by_slug.get(slug)
    if sci is None:
        return None
    cat = db.execute(select(Category).where(Category.slug == "synthetic-plants")).scalars().first()
    if cat is None:
        return None
    return (
        db.execute(select(Task).where(Task.title == _synth_title(sci), Task.category_id == cat.id))
        .scalars()
        .first()
    )


def seed_synthetic_plants(db: Session) -> dict:
    """Idempotent: a 'synthetic-plants' category, a 'botanical_plausibility' criterion, one
    Task per plant type, and the 'pd-archetype' procedural generator. Votes-only — the
    existing arena + Bradley-Terry leaderboard rank generators by botanical plausibility."""
    cat = db.execute(select(Category).where(Category.slug == "synthetic-plants")).scalars().first()
    if cat is None:
        cat = Category(
            slug="synthetic-plants",
            name="Synthetic Plants",
            description="Procedurally/AI-generated 3D plants, judged on botanical plausibility.",
        )
        db.add(cat)
        db.flush()

    crit = (
        db.execute(select(Criterion).where(Criterion.slug == "botanical_plausibility"))
        .scalars()
        .first()
    )
    if crit is None:
        db.add(
            Criterion(
                slug="botanical_plausibility",
                name="Botanical plausibility",
                description="Which looks more like a botanically real plant?",
            )
        )

    n_tasks = 0
    for _slug, sci_name in SYNTH_TYPES:
        title = _synth_title(sci_name)
        task = (
            db.execute(select(Task).where(Task.title == title, Task.category_id == cat.id))
            .scalars()
            .first()
        )
        if task is None:
            db.add(
                Task(
                    category_id=cat.id,
                    title=title,
                    prompt=f"Generate a botanically plausible 3D model of {sci_name}.",
                    criteria_note="Ranked by pairwise 'more botanically plausible?' votes (Mode-A).",
                )
            )
        n_tasks += 1

    gen = db.execute(select(Generator).where(Generator.slug == "pd-archetype")).scalars().first()
    if gen is None:
        db.add(
            Generator(
                slug="pd-archetype",
                name="PD archetype (procedural)",
                kind="model",
                is_anonymous=True,
            )
        )

    db.flush()
    return {"tasks": n_tasks, "generators": 1}


# Volumetric-modality subjects (CT/MRI). Cereal stand-in for the maize volumetric gap.
VOLUMETRIC_SUBJECTS = [
    (
        "Hordeum vulgare — barley root system (3D MRI)",
        "Volumetric MRI reference of a barley root system (marching-cubes iso-surface).",
    ),
]


# Rose (Track A3) subject — genus-level (the spotlight mixes Rosa species: rugosa scan/volume,
# multiflora/chinensis found). A plain subject (no ReconTask) so recon GT scoring isn't attempted
# (rose has no GT bundle); found/scan/volumetric/procedural outputs all attach here by title.
ROSE_SUBJECT_TITLE = "Rosa — single-image → 3D reconstruction"


def _ensure_subject(db: Session, title: str, prompt: str) -> int:
    """Idempotent: ensure the 'plants' category + a plain subject Task (no ReconTask, so recon GT
    scoring isn't attempted) exists. Returns 1 if created, else 0."""
    cat = db.execute(select(Category).where(Category.slug == "plants")).scalars().first()
    if cat is None:
        cat = Category(slug="plants", name="Plants", description="Whole plants (image→3D recon)")
        db.add(cat)
        db.flush()
    task = (
        db.execute(select(Task).where(Task.title == title, Task.category_id == cat.id))
        .scalars()
        .first()
    )
    if task is None:
        db.add(Task(category_id=cat.id, title=title, prompt=prompt))
        db.flush()
        return 1
    db.flush()
    return 0


def seed_rose_subject(db: Session) -> dict:
    """Idempotent: ensure the Rosa subject Task exists (home for rose found/scan/volumetric/procedural)."""
    n = _ensure_subject(
        db,
        ROSE_SUBJECT_TITLE,
        "Reconstruct a 3D model of a rose (Rosa) plant in bloom from a single RGB image.",
    )
    return {"subjects": n}


# Soybean (Track A2 legume) subject. Soybean-specific found/procedural/recon; the scan tier uses a
# CC-BY common-bean legume point-cloud stand-in (no open-licensed soybean scan — Soybean-MVS is unmarked).
SOYBEAN_SUBJECT_TITLE = "Glycine max — single-image → 3D reconstruction"


def seed_soybean_subject(db: Session) -> dict:
    """Idempotent: ensure the Glycine max (soybean) subject Task exists."""
    n = _ensure_subject(
        db,
        SOYBEAN_SUBJECT_TITLE,
        "Reconstruct a 3D model of a soybean (Glycine max) plant from a single RGB image.",
    )
    return {"subjects": n}


def seed_volumetric_subjects(db: Session) -> dict:
    """Idempotent: ensure the 'plants' category + each volumetric subject Task exists, so a
    volumetric GLB ingested onto the subject has a home and a spotlight to surface it."""
    cat = db.execute(select(Category).where(Category.slug == "plants")).scalars().first()
    if cat is None:
        cat = Category(slug="plants", name="Plants", description="Whole plants (image→3D recon)")
        db.add(cat)
        db.flush()
    n = 0
    for title, prompt in VOLUMETRIC_SUBJECTS:
        task = (
            db.execute(select(Task).where(Task.title == title, Task.category_id == cat.id))
            .scalars()
            .first()
        )
        if task is None:
            db.add(Task(category_id=cat.id, title=title, prompt=prompt))
            n += 1
    db.flush()
    return {"subjects": n}


# (slug, name, description)
# Top-level taxonomy is the tree of life (one consistent axis). Plants is the flagship
# active domain (AgriGen's focus); the rest are visible "coming soon" placeholders — a
# category with no tasks renders as coming-soon (no schema flag needed). Scale/anatomy
# (whole organism, organ, cell) is a property of the Task, not a category.
CATEGORIES = [
    (
        "plants",
        "Plants",
        "Plants — whole organisms, organs, and cells. AgriGen's focus and the flagship domain.",
    ),
    ("fungi", "Fungi", "Fungi — mushrooms, brackets, and hyphal structures. Coming soon."),
    ("animals", "Animals", "Animals — anatomy and whole organisms. Coming soon."),
    ("microbes", "Microbes", "Microbes — bacteria, protists, and single-celled life. Coming soon."),
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

# (slug, category_slug, title, prompt, shape) — all demo tasks render to GLB meshes.
TASKS = [
    (
        "plant-cell",
        "plants",
        "Plant cell with organelles",
        "Generate a 3D model of a plant cell showing the membrane and a nucleus.",
        "cell",
    ),
    (
        "rose-bloom",
        "plants",
        "Rose flower in bloom",
        "Generate a 3D model of an open rose flower with layered petals.",
        "flower",
    ),
    (
        "wheat-root",
        "plants",
        "Wheat seedling root system",
        "Generate a 3D model of a branching wheat root system.",
        "root",
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
            for model in _FORCE_DELETE_MODELS:
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
        for tslug, cslug, title, prompt, shape in TASKS:
            t = Task(category_id=cats[cslug].id, title=title, prompt=prompt)
            db.add(t)
            db.flush()
            task_by_slug[tslug] = (t, shape)
            for gslug, gen in gens.items():
                seed = _seed_int(tslug, gslug)
                rel = Path("seed") / f"{tslug}__{gslug}.glb"
                meta = build_asset(shape, seed, config.ASSET_DIR / rel)
                _publish(rel)
                meta["generator"] = gslug
                db.add(
                    ModelOutput(
                        task_id=t.id,
                        generator_id=gen.id,
                        title=f"{title} — {gen.name}",
                        asset_path=str(rel).replace("\\", "/"),
                        asset_format="glb",
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

        # Recon Mode-B benchmark scaffolding (species Tasks + slug mapping + method gens),
        # so ingested recon GLBs are scorable immediately.
        seed_recon_benchmark(db)
        seed_synthetic_plants(db)
        seed_volumetric_subjects(db)
        seed_rose_subject(db)
        seed_soybean_subject(db)

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
