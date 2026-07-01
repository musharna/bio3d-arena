"""DB wiring for the Mode-B organ-structure fidelity axis (botanical_organ_fidelity_v1).

The structure-known counterpart to recon_service (which scores recon *appearance* vs GT). For
each STRUCTURE-KNOWN (procedural) ModelOutput that carries a declared organ record, POST the
record to AgriGen's /score_structure and upsert an OrganMetric row (one per output). Recon/
scan/found/volumetric outputs are N/A — no declared structure — and get no row (rendered "—").

Mirrors recon_service's concurrency discipline: the (multi-second) scorer HTTP round-trip runs
FIRST with read-only DB access; the OrganMetric write holds the SQLite write lock only for the
millisecond field-mapping + flush, never across the network call. Best-effort: a scorer/IO
failure stores status='error' and never raises (so a batch never aborts on one output).
"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config
from .models import ModelOutput, OrganMetric
from .recon_client import ScorerError, score_structure
from .sourcing import source_class
from .storage import get_storage
from .structure_records import VARIANT_SPECIES, load_sidecar, seed_record_for_species

MESH_FORMATS = {"glb", "gltf"}


class ScoringDisabled(RuntimeError):
    """Raised by the default scorer when SCORING_ENABLED is False (public instance)."""


def _slug(species: str) -> str:
    """Canonical species slug — 'Zea mays' and 'zea_mays' both → 'zea_mays' (mirrors the
    AgriGen-side slugifier; the endpoint slugifies too, this keeps our stored slug consistent)."""
    return species.strip().lower().replace(" ", "_")


def resolve_record(db: Session, output: ModelOutput) -> tuple[dict | None, str]:
    """The declared organ record (a /score_structure request body) for a structure-known output,
    plus the resolved species slug. Returns (None, "") when no record is declared.

    Source priority (§ contract): a ``…__structure.json`` sidecar next to the GLB (verbatim) →
    else a seed-PD record keyed by the species slug resolved from the output's recon Task
    (ReconTask.species_slug) → else from the procedural generator's meta_json 'variant'."""
    storage = get_storage()
    sidecar = load_sidecar(storage, output.asset_path)
    if sidecar is not None:
        return sidecar, _slug(str(sidecar.get("species", "")))

    # Resolve species: prefer the recon Task's GT-bundle slug (authoritative), else the
    # procedural generator's declared variant.
    from .recon_service import species_slug_for_task

    slug = species_slug_for_task(db, output.task_id)
    if not slug:
        try:
            variant = json.loads(output.meta_json or "{}").get("variant")
        except (ValueError, TypeError):
            variant = None
        slug = VARIANT_SPECIES.get(variant) if variant else None
    record = seed_record_for_species(slug)
    return record, (slug or "")


def _default_scorer(record: dict) -> dict:
    """Live /score_structure call. Injectable so tests run without the microservice."""
    if not config.SCORING_ENABLED:
        raise ScoringDisabled("scoring disabled on this instance (empty RECON_SCORER_URL)")
    return score_structure(record, base_url=config.RECON_SCORER_URL)


def _get_or_create(db: Session, output_id: int) -> OrganMetric:
    m = db.execute(select(OrganMetric).where(OrganMetric.output_id == output_id)).scalars().first()
    if m is None:
        m = OrganMetric(output_id=output_id)
        db.add(m)
        db.flush()
    return m


def score_and_store(
    db: Session, output: ModelOutput, *, scorer=_default_scorer
) -> OrganMetric | None:
    """Score one structure-known output's organ record → upsert its OrganMetric (best-effort).

    Returns None (and writes NO row) for outputs that are N/A on this axis: non-procedural
    source-classes (recon/scan/found/volumetric), or procedural outputs with no declared
    organ record (un-covered species, no sidecar). That NULL renders "—" on the board.
    """
    if source_class(output.source) != "procedural":
        return None  # N/A: no declared organ structure (do NOT extract from mesh)

    record, slug = resolve_record(db, output)  # read-only; no write lock held
    if record is None:
        return None  # structure-known source but no declared record yet → "—"

    card: dict | None = None
    scorer_err: str | None = None
    try:
        card = scorer(record)
    except ScorerError as e:
        scorer_err = str(e)
    except Exception as e:  # noqa: BLE001 — best-effort; capture and continue the batch
        scorer_err = str(e)

    # --- write phase: short-lived lock only ---
    m = _get_or_create(db, output.id)
    m.species_slug = slug
    if scorer_err is not None or card is None:
        m.status = "error"
        m.detail = scorer_err or "structure scorer returned no card"
        m.botanical_fidelity = None
        db.flush()
        return m
    try:
        fidelity = card.get("botanical_fidelity")
        m.botanical_fidelity = fidelity
        m.n_attributes = int(card.get("n_attributes") or 0)
        m.attributes = json.dumps(card.get("attributes") or {})
        m.note = str(card.get("note") or "")
        # A 200 with a null fidelity + note = the species has no botanical reference (honest
        # N/A). A null fidelity with attributes graded would be a bug; treat note as the signal.
        if fidelity is None:
            m.status = "no_reference"
        else:
            m.status = "scored"  # incl. an honest 0.0 structural gap (a valid finding)
        # The endpoint echoes the resolved slug; prefer it (handles binomial sidecar species).
        m.species_slug = str(card.get("species") or slug)
        m.detail = ""
    except Exception as e:  # noqa: BLE001 — best-effort
        m.status = "error"
        m.detail = str(e)
    db.flush()
    return m


def rescore_all(db: Session, *, scorer=_default_scorer) -> dict:
    """Batch: (re)score every structure-known output's organ fidelity. Commits per-output so a
    concurrent arena request never waits on the whole run (the write lock is held for a single
    output's write, not the run)."""
    outs = db.execute(select(ModelOutput).where(ModelOutput.is_gold.is_(False))).scalars().all()
    scored = errors = skipped = 0
    for o in outs:
        if (o.asset_format or "").lower() not in MESH_FORMATS:
            skipped += 1
            continue
        m = score_and_store(db, o, scorer=scorer)
        if m is None:
            skipped += 1  # N/A axis (non-procedural or no declared record)
            continue
        if m.status == "error":
            errors += 1
        else:
            scored += 1  # 'scored' or 'no_reference' — both are successful service answers
        db.commit()
    return {"outputs": len(outs), "scored": scored, "errors": errors, "skipped": skipped}
