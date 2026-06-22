"""Subject Spotlight: deterministic failure-flag derivation + page-data assembly.

A Spotlight is a curated deep-dive on one benchmark subject, showing every model we
have for it with all metrics, failure flags, and (Phase 2) critic notes. Internal
inspection tool — see docs/superpowers/specs/2026-06-21-subject-spotlight-design.md.
"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Critique, Generator, Metric, ModelOutput, Task
from .sourcing import source_class
from .storage import get_storage

# Tunable thresholds (initial; see spec §Components).
COVERAGE_MIN = 0.5
FSCORE_MIN = 0.5


def derive_flags(metric: Metric | None) -> list[tuple[str, str]]:
    """Deterministic failure/ok flags from a Metric. Each flag is (kind, label);
    kind drives a CSS severity class. Never raises."""
    if metric is None or metric.status != "ok" or metric.chamfer is None:
        return [("unscored", "no objective score")]
    flags: list[tuple[str, str]] = []
    lo, hi, ch = metric.gt_band_lo, metric.gt_band_hi, metric.chamfer
    if hi is not None and ch > hi:
        flags.append(("shape", "outside natural variation"))
    elif lo is not None and hi is not None and lo <= ch <= hi:
        flags.append(("ok", "within natural variation"))
    if metric.coverage is not None and metric.coverage < COVERAGE_MIN:
        flags.append(("coverage", "missing geometry"))
    if metric.fscore is not None and metric.fscore < FSCORE_MIN:
        flags.append(("surface", "low F-score@τ"))
    return flags or [("ok", "scored")]


# Curated subjects (internal, hand-picked). reference_image is an optional public
# real-photo path under the asset store; None ⇒ no reference image (Phase 1).
SPOTLIGHTS: list[dict] = [
    {
        "slug": "tomato",
        "task_title": "Solanum lycopersicum — single-image → 3D reconstruction",
        "featured": True,
        "order": 0,
        "blurb": "How current image→3D models handle a whole tomato plant.",
        "reference_image": "reference/tomato_ref.jpg",
        # CC-BY-SA-4.0 input photo — attribution shown in the panel (see docs/ATTRIBUTIONS.md).
        "reference_credit": "Photo: Kolforn / Wikimedia Commons, CC-BY-SA-4.0",
    },
    {
        "slug": "arabidopsis",
        "task_title": "Arabidopsis thaliana — single-image → 3D reconstruction",
        "featured": False,
        "order": 1,
        "blurb": "Thale cress rosette — fine structure stress test.",
        "reference_image": None,
    },
]


def find_spotlight(slug: str) -> dict | None:
    return next((s for s in SPOTLIGHTS if s["slug"] == slug), None)


def _metrics_dict(m: Metric | None) -> dict:
    return {
        "chamfer": m.chamfer if m else None,
        "fscore": m.fscore if m else None,
        "coverage": m.coverage if m else None,
        "tau": m.tau if m else None,
        "gt_band_lo": m.gt_band_lo if m else None,
        "gt_band_hi": m.gt_band_hi if m else None,
        "within_variation": m.species_verdict if m else None,
    }


def build_spotlight(db: Session, slug: str) -> dict | None:
    spot = find_spotlight(slug)
    if spot is None:
        return None
    task = db.execute(select(Task).where(Task.title == spot["task_title"])).scalars().first()
    if task is None:
        return None
    storage = get_storage()
    outs = (
        db.execute(
            select(ModelOutput).where(
                ModelOutput.task_id == task.id, ModelOutput.is_gold.is_(False)
            )
        )
        .scalars()
        .all()
    )
    models = []
    for o in outs:
        metric = db.execute(select(Metric).where(Metric.output_id == o.id)).scalars().first()
        crit = db.execute(select(Critique).where(Critique.output_id == o.id)).scalars().first()
        gen = db.get(Generator, o.generator_id)
        found = o.source != "bio3d-arena"
        meta = json.loads(o.meta_json or "{}")
        depiction = meta.get("depiction")
        cls = source_class(o.source)
        dataset = meta.get("dataset")
        render = meta.get("render", "mesh")
        label = o.title if (found and o.title) else (gen.name if gen else "?")
        models.append(
            {
                "id": o.id,  # distinguishes multiple outputs from the same generator
                "generator": gen.slug if gen else "?",
                "generator_name": gen.name if gen else "?",
                "cls": cls,
                "dataset": dataset,
                "render": render,
                "found": found,
                "label": label,
                "depiction": depiction,
                "caveat": meta.get("caveat"),
                "format": o.asset_format,
                "asset_url": storage.url_for(o.asset_path),
                "thumbnail_url": storage.url_for(crit.render_path)
                if crit and crit.render_path
                else None,
                "metrics": _metrics_dict(metric),
                "flags": derive_flags(metric),
                "critic_note": crit.critic_note if crit else "",
                "provenance": {
                    "source": o.source,
                    "license": o.license,
                    "attribution": o.attribution,
                    "external_url": o.external_url,
                },
            }
        )
    return {
        "slug": spot["slug"],
        "title": spot["task_title"],
        "blurb": spot["blurb"],
        "featured": spot["featured"],
        "reference_image": (
            storage.url_for(spot["reference_image"]) if spot["reference_image"] else None
        ),
        "reference_credit": spot.get("reference_credit"),
        "models": models,
    }
