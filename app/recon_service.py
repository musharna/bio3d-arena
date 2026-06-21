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
    """Live scorer for the batch path. NOTE (B5 gap): the service GT bundle is keyed by
    SPECIES SLUG, not bio3d Task PK — until Tasks carry a species slug (seed/B5), this
    passes the PK as the slug and the service answers 404 (clear, fail-loud). Inject a
    scorer that supplies the real slug to score live before B5 lands."""
    return score_output(glb_bytes, str(task_id), base_url=config.RECON_SCORER_URL)


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
        # Live §11 envelope: {task_id, bundle_version, metrics:{..., params:{...}}}.
        metrics = card.get("metrics") or {}
        params = metrics.get("params") or {}
        m.chamfer = metrics.get("chamfer")
        # `chamfer` IS the ICP-matched min distance to the GT set (with matched_gt_index).
        m.nearest_shape_distance = metrics.get("chamfer")
        m.nearest_gt_idx = metrics.get("matched_gt_index")
        m.fscore = metrics.get("fscore_at_tau")
        m.tau = params.get("tau")
        m.coverage = metrics.get("coverage")
        # The live service returns no PASS/FAIL verdict or GT-LOO band — leave null
        # (the /benchmark verdict + GT-band columns render '—' until a band channel ships).
        m.species_verdict = metrics.get("species_verdict")
        m.gt_band_lo = None
        m.gt_band_hi = None
        m.point_count = params.get("n_points")
        m.icp_seed = params.get("seed")
        m.scorer_version = str(params.get("metric") or "")
        m.gt_version_hash = card.get("bundle_version") or ""  # D2 leakage pin (sha256)
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


# --- Mode-B aggregations for the /benchmark page ----------------------------


def _gen_name(db: Session, gid: int) -> str:
    from .models import Generator

    g = db.get(Generator, gid)
    return g.name if g else str(gid)


def recon_leaderboard(db: Session, task_id: int) -> list[dict]:
    """Per-output objective scores for a task, best (lowest chamfer) first."""
    outs = (
        db.execute(
            select(ModelOutput).where(
                ModelOutput.task_id == task_id, ModelOutput.is_gold.is_(False)
            )
        )
        .scalars()
        .all()
    )
    rows = []
    for o in outs:
        m = db.execute(select(Metric).where(Metric.output_id == o.id)).scalars().first()
        if m is None or m.status != "ok":
            continue
        rows.append(
            {
                "generator": _gen_name(db, o.generator_id),
                "chamfer": m.chamfer,
                "fscore": m.fscore,
                "coverage": m.coverage,
                "species_verdict": m.species_verdict,
                "gt_band": [m.gt_band_lo, m.gt_band_hi],
            }
        )
    rows.sort(
        key=lambda r: (r["chamfer"] is None, r["chamfer"] if r["chamfer"] is not None else 0.0)
    )
    return rows


def _spearman(a: list[float], b: list[float]) -> float | None:
    """Spearman rank correlation. Inputs are already ranks (1..n) → Pearson of them."""
    import numpy as np

    if len(a) < 2:
        return None
    x, y = np.array(a, dtype=float), np.array(b, dtype=float)
    if x.std() == 0 or y.std() == 0:
        return None
    return round(float(np.corrcoef(x, y)[0, 1]), 3)


def agreement(db: Session, task_id: int) -> dict:
    """Vote↔metric agreement: metric rank (chamfer asc) vs Mode-A BT rank (bt desc).

    Local rank-correlation only — the microservice boundary (D1) forbids importing
    AgriGen's richer 2AFC eval/agreement.py; that becomes a service-returned field later.
    """
    from .models import Rating, Task

    task = db.get(Task, task_id)
    cat_id = task.category_id if task else None
    outs = (
        db.execute(
            select(ModelOutput).where(
                ModelOutput.task_id == task_id, ModelOutput.is_gold.is_(False)
            )
        )
        .scalars()
        .all()
    )

    # Collapse to one entry per GENERATOR (keyed by stable generator_id, not display
    # name) — a generator may have multiple outputs on a task and would otherwise be
    # double-counted in the rank lists, distorting the Spearman. Keep the best (lowest)
    # chamfer per generator, consistent with "this method's best recon".
    best: dict[int, dict] = {}
    for o in outs:
        m = db.execute(select(Metric).where(Metric.output_id == o.id)).scalars().first()
        if m is None or m.status != "ok" or m.chamfer is None:
            continue
        gid = o.generator_id
        if gid not in best or m.chamfer < best[gid]["chamfer"]:
            rating = (
                db.execute(
                    select(Rating)
                    .where(
                        Rating.generator_id == gid,
                        (Rating.category_id == cat_id) | (Rating.category_id.is_(None)),
                    )
                    .order_by(Rating.category_id.is_(None))  # prefer category-scoped over global
                )
                .scalars()
                .first()
            )
            best[gid] = {
                "generator_id": gid,
                "generator": _gen_name(db, gid),
                "chamfer": m.chamfer,
                "bt": rating.bt_score if rating else None,
            }
    entries = list(best.values())

    by_chamfer = sorted(entries, key=lambda e: e["chamfer"])  # lower = better
    metric_rank = {e["generator_id"]: i + 1 for i, e in enumerate(by_chamfer)}
    with_bt = sorted(
        [e for e in entries if e["bt"] is not None],
        key=lambda e: -e["bt"],  # higher = better
    )
    vote_rank = {e["generator_id"]: i + 1 for i, e in enumerate(with_bt)}

    common = [e["generator_id"] for e in entries if e["generator_id"] in vote_rank]
    sp = _spearman([metric_rank[g] for g in common], [vote_rank[g] for g in common])
    rows = [
        {
            "generator": e["generator"],
            "metric_rank": metric_rank[e["generator_id"]],
            "vote_rank": vote_rank.get(e["generator_id"]),
        }
        for e in by_chamfer
    ]
    return {"spearman": sp, "n": len(common), "rows": rows}
