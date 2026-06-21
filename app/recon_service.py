"""DB wiring for Mode-B recon scoring: call the scorer, map the contract bundle into a
Metric row (upsert by output_id), best-effort. Mirrors validation_service / the
/admin/revalidate batch shape — the objective counterpart to the vote-driven Rating.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config
from .models import Metric, ModelOutput
from .recon_client import score_output
from .storage import get_storage

MESH_FORMATS = {"glb", "gltf"}


def _default_scorer(glb_bytes: bytes, task_id: int) -> dict:
    return score_output(glb_bytes, task_id, base_url=config.RECON_SCORER_URL)


def _get_or_create_metric(db: Session, output_id: int) -> Metric:
    m = db.execute(select(Metric).where(Metric.output_id == output_id)).scalars().first()
    if m is None:
        m = Metric(output_id=output_id)
        db.add(m)
        db.flush()
    return m


def score_and_store(db: Session, output: ModelOutput, *, scorer=_default_scorer) -> Metric:
    """Score one output's GLB vs its task GT and upsert a Metric row (best-effort).

    `scorer(glb_bytes, task_id) -> dict` is injectable so tests run without the live
    microservice. A scorer/read failure stores status='error' and never raises.
    """
    m = _get_or_create_metric(db, output.id)
    try:
        glb = get_storage().read(output.asset_path)
        card = scorer(glb, output.task_id)
        conf = card.get("confounds", {}) or {}
        band = card.get("gt_band", {}) or {}
        m.chamfer = card.get("chamfer")
        m.nearest_shape_distance = card.get("nearest_shape_distance")
        m.nearest_gt_idx = card.get("nearest_gt_idx")
        m.fscore = card.get("fscore_at_tau")
        m.tau = card.get("tau")
        m.coverage = card.get("coverage")
        m.species_verdict = card.get("species_verdict")
        m.gt_band_lo = band.get("lo")
        m.gt_band_hi = band.get("hi")
        m.point_count = conf.get("point_count")
        m.icp_seed = conf.get("icp_seed")
        m.scorer_version = conf.get("scorer_version", "")
        m.gt_version_hash = conf.get("gt_version_hash", "")
        m.status = "ok"
        m.detail = ""
    except Exception as e:  # noqa: BLE001 — best-effort; capture and continue the batch
        m.status = "error"
        m.detail = str(e)
    db.flush()
    return m


def rescore_all(db: Session, *, scorer=_default_scorer) -> dict:
    """Batch: score every non-gold mesh output. Non-mesh (PDB/SDF) outputs are skipped."""
    outs = db.execute(select(ModelOutput).where(ModelOutput.is_gold.is_(False))).scalars().all()
    scored = errors = skipped = 0
    for o in outs:
        if (o.asset_format or "").lower() not in MESH_FORMATS:
            skipped += 1
            continue
        m = score_and_store(db, o, scorer=scorer)
        if m.status == "ok":
            scored += 1
        else:
            errors += 1
    db.commit()
    return {"outputs": len(outs), "scored": scored, "errors": errors, "skipped": skipped}
