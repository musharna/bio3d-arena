"""Plant growth-form taxonomy + per-form capture/recon STRATEGY + the per-subject seed.

The STRATEGY table is the *rules* (recipe per growth form), kept in code so it is versioned
and testable. PlantMorphology (DB) stores only the per-subject classification. Encodes the
multi-view e2e caveat: top-down NVS views make a flat rosette droop, so rosette = single-preferred."""

from __future__ import annotations

from dataclasses import dataclass

# --- growth-form taxonomy (active forms have a STRATEGY entry) ---
ROSETTE = "rosette"
ERECT_HERB = "erect_herb"
GRAMINOID = "graminoid"
SHRUB = "shrub"
TREE_CONIFER = "tree_conifer"
VINE_SPRAWLING = "vine_sprawling"
# reserved for future subjects (no STRATEGY entry yet)
TREE_BROADLEAF = "tree_broadleaf"
SUCCULENT = "succulent"

GROWTH_FORMS = {
    ROSETTE,
    ERECT_HERB,
    GRAMINOID,
    SHRUB,
    TREE_CONIFER,
    VINE_SPRAWLING,
    TREE_BROADLEAF,
    SUCCULENT,
}


@dataclass(frozen=True)
class StrategyEntry:
    capture_view: str
    background: str
    framing: str
    recon_mode: str  # single | multiview | multiview_preferred | multiview_required
    nvs_pose_hint: str
    expected_failure: str
    min_px: int = 1024


_BG = "plain/neutral background"
_FRAME = "subject centered, fills >50% of frame, soft even light"

STRATEGY: dict[str, StrategyEntry] = {
    ROSETTE: StrategyEntry(
        capture_view="top-down (radially flat — natural for a rosette)",
        background=_BG,
        framing=_FRAME,
        recon_mode="single",
        nvs_pose_hint="multi-view droops: top-down NVS views give the recon no flat-ground "
        "constraint, so leaves cascade downward. If multi-view, bias NVS to side/mid elevations.",
        expected_failure="single-image: flat but acceptable; multi-view: over-tall / drooping leaves",
    ),
    ERECT_HERB: StrategyEntry(
        capture_view="three-quarter or front, full height",
        background=_BG,
        framing=_FRAME,
        recon_mode="single",
        nvs_pose_hint="default NVS poses fine; multi-view helps recover occluded stems",
        expected_failure="thin stems/petioles may thin out in single-image",
    ),
    GRAMINOID: StrategyEntry(
        capture_view="front, full height",
        background=_BG,
        framing=_FRAME,
        recon_mode="multiview_preferred",
        nvs_pose_hint="thin vertical blades need lateral views; default NVS azimuths are adequate",
        expected_failure="single-image loses thin blades / collapses the canopy",
    ),
    SHRUB: StrategyEntry(
        capture_view="three-quarter view",
        background=_BG,
        framing=_FRAME,
        recon_mode="single",
        nvs_pose_hint="multi-view recovers the occluded interior of a dense bloom canopy",
        expected_failure="interior occlusion; dense bloom can read as a solid blob",
    ),
    TREE_CONIFER: StrategyEntry(
        capture_view="front, full tree",
        background=_BG,
        framing=_FRAME,
        recon_mode="multiview_required",
        nvs_pose_hint="needles are a fundamental single-image failure; even multi-view is hard — "
        "treat results as low-confidence",
        expected_failure="single-image blobs the needle canopy (confirmed on pine)",
    ),
    VINE_SPRAWLING: StrategyEntry(
        capture_view="isolate one representative section",
        background=_BG,
        framing=_FRAME,
        recon_mode="multiview",
        nvs_pose_hint="sprawling habit is hard to frame as one subject; prefer a bounded section",
        expected_failure="ambiguous extent; recon may fuse separate stems",
    ),
}

# subject_slug (CROPS key) -> growth_form. tomato: indeterminate field tomatoes are vining,
# but our reference is a potted, front-on specimen, so ERECT_HERB.
SEED: dict[str, str] = {
    "arabidopsis": ROSETTE,
    "maize": GRAMINOID,
    "soybean": ERECT_HERB,
    "tomato": ERECT_HERB,
    "rose": SHRUB,
    "pinus": TREE_CONIFER,
}


def seed_morphology(db) -> int:
    """Idempotent upsert of SEED into PlantMorphology. Returns the number of rows created or
    changed. Never overwrites a `notes` field; only sets growth_form to the seed value."""
    from sqlalchemy import select

    from app.models import PlantMorphology

    touched = 0
    for slug, form in SEED.items():
        row = db.execute(
            select(PlantMorphology).where(PlantMorphology.subject_slug == slug)
        ).scalar_one_or_none()
        if row is None:
            db.add(PlantMorphology(subject_slug=slug, growth_form=form))
            touched += 1
        elif row.growth_form != form:
            row.growth_form = form
            touched += 1
    db.commit()
    return touched
