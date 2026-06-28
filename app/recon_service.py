"""DB wiring for Mode-B recon scoring: call the scorer, map the contract bundle into a
Metric row (upsert by output_id), best-effort — the objective counterpart to the
vote-driven Rating.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config
from .models import Metric, ModelOutput, OrganMetric
from .recon_client import score_output
from .storage import get_storage

MESH_FORMATS = {"glb", "gltf"}


def species_slug_for_task(db: Session, task_id: int) -> str | None:
    """The GT-bundle species slug bound to a Task (via ReconTask), or None if the Task
    is not a Mode-B recon benchmark. This is the key the scoring service resolves GT by."""
    from .models import ReconTask

    rt = db.execute(select(ReconTask).where(ReconTask.task_id == task_id)).scalars().first()
    return rt.species_slug if rt else None


def _default_scorer(glb_bytes: bytes, species_slug: str | None) -> dict:
    """Live scorer for the batch path. `species_slug` is the GT-bundle key resolved from
    the output's Task (ReconTask). rescore_all only reaches this for recon-benchmark
    outputs (slug present); a None slug here would 404 (fail-loud)."""
    return score_output(glb_bytes, str(species_slug), base_url=config.RECON_SCORER_URL)


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

    Ordering matters for concurrency: the (multi-second) scorer HTTP round-trip runs
    FIRST, with only read-only DB access (the species-slug lookup). The Metric row is
    created/written ONLY after the card is in hand — so the SQLite write lock is held for
    the local field-mapping + flush (milliseconds), never across the network call. Holding
    the write lock across the RPC was what starved concurrent /api/next requests.
    """
    slug = species_slug_for_task(db, output.task_id)  # read-only; no write lock held
    card: dict | None = None
    scorer_err: str | None = None
    try:
        glb = get_storage().read(output.asset_path)
        # The scorer resolves GT by SPECIES SLUG (not the bio3d Task PK); injected fakes
        # ignore the second arg, so unit tests pass with a None slug.
        card = scorer(glb, slug)
    except Exception as e:  # noqa: BLE001 — best-effort; capture and continue the batch
        scorer_err = str(e)

    # --- write phase: short-lived lock only ---
    m = _get_or_create_metric(db, output.id)
    if scorer_err is not None or card is None:
        m.status = "error"
        m.detail = scorer_err or "scorer returned no card"
        db.flush()
        return m
    try:
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
        # GT natural-variation band (gt-to-gt LOO) + within-variation verdict — the
        # methodology's holistic measure: "is the recon within real conspecific shape
        # variation?" (chamfer vs the P75 band), not just "is the number small?".
        band = card.get("band") or {}
        m.gt_band_lo = band.get("band_median")
        m.gt_band_hi = band.get("band_p75")
        wv = card.get("within_variation")
        m.species_verdict = (
            ("PASS" if wv else "FAIL") if wv is not None else metrics.get("species_verdict")
        )
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
    """Batch: score every non-gold mesh output that belongs to a recon-benchmark Task.

    Skips non-mesh (PDB/SDF) outputs AND mesh outputs whose Task has no species slug
    (the demo meshes) — only Mode-B recon-benchmark GLBs are scored against the service.
    """
    outs = db.execute(select(ModelOutput).where(ModelOutput.is_gold.is_(False))).scalars().all()
    scored = errors = skipped = 0
    for o in outs:
        if (o.asset_format or "").lower() not in MESH_FORMATS:
            skipped += 1
            continue
        if species_slug_for_task(db, o.task_id) is None:
            skipped += 1  # not a recon-benchmark task
            continue
        m = score_and_store(db, o, scorer=scorer)
        if m.status == "ok":
            scored += 1
        else:
            errors += 1
        db.commit()  # per-output: release the SQLite write lock between outputs so a
        # concurrent arena request never waits on the whole batch (the lock is held for a
        # single output's write, not the minutes-long run).
    return {"outputs": len(outs), "scored": scored, "errors": errors, "skipped": skipped}


# --- Mode-B aggregations for the /benchmark page ----------------------------


def _gen_name(db: Session, gid: int) -> str:
    from .models import Generator
    from .service import generator_display_names

    # Disambiguate generators that share a display name (e.g. the 8 XfrogPlants variants).
    name = generator_display_names(db).get(gid)
    if name is not None:
        return name
    g = db.get(Generator, gid)
    return g.name if g else str(gid)


def recon_outputs_for_task(db: Session, task_id: int) -> list[dict]:
    """Viewable recon GLBs for a task (one per output) — for the B4 side-by-side viewer."""
    outs = (
        db.execute(
            select(ModelOutput).where(
                ModelOutput.task_id == task_id,
                ModelOutput.is_gold.is_(False),
            )
        )
        .scalars()
        .all()
    )
    storage = get_storage()
    rows = []
    for o in outs:
        if (o.asset_format or "").lower() not in MESH_FORMATS:
            continue
        m = db.execute(select(Metric).where(Metric.output_id == o.id)).scalars().first()
        rows.append(
            {
                "generator": _gen_name(db, o.generator_id),
                "url": storage.url_for(o.asset_path),
                "format": o.asset_format,
                "chamfer": m.chamfer if (m and m.status == "ok") else None,
            }
        )
    rows.sort(
        key=lambda r: (r["chamfer"] is None, r["chamfer"] if r["chamfer"] is not None else 0.0)
    )
    return rows


def reference_for_task(db: Session, task_id: int) -> dict | None:
    """The reference to show beside a reconstruction, in priority order:

    1. An explicit exemplar (Task.reference_asset_id), if set.
    2. The held-out GT scan for the task's species, baked to a GLB by scripts/render_gt.py
       (internal build — the real-plant scan the Mode-B scorer grades against).

    Returns a dict with `is_gt` True for case 2 so the panel can label it accordingly.
    """
    from .gt_render import find_gt_glb
    from .models import ReconTask, Task

    task = db.get(Task, task_id)
    if task and task.reference_asset_id:
        ref = db.get(ModelOutput, task.reference_asset_id)
        if ref:
            return {
                "url": get_storage().url_for(ref.asset_path),
                "format": ref.asset_format,
                "is_gt": False,
            }

    rt = db.execute(select(ReconTask).where(ReconTask.task_id == task_id)).scalars().first()
    if rt and rt.species_slug:
        rel = find_gt_glb(rt.species_slug)
        if rel:
            return {"url": get_storage().url_for(rel), "format": "glb", "is_gt": True}
    return None


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


def recon_method_leaderboard(db: Session, task_id: int) -> list[dict]:
    """Per-METHOD board: collapse a method's N recons (median-of-N expansion) into one row
    with n + mean ± std chamfer (error bars) + best. Degrades to n=1 (std None) today."""
    import statistics
    from collections import defaultdict

    outs = (
        db.execute(
            select(ModelOutput).where(
                ModelOutput.task_id == task_id, ModelOutput.is_gold.is_(False)
            )
        )
        .scalars()
        .all()
    )
    by_gen: dict[int, list[Metric]] = defaultdict(list)
    organ_by_gen: dict[int, list[OrganMetric]] = defaultdict(list)
    for o in outs:
        m = db.execute(select(Metric).where(Metric.output_id == o.id)).scalars().first()
        if m is not None and m.status == "ok" and m.chamfer is not None:
            by_gen[o.generator_id].append(m)
        # The organ-fidelity axis is independent of chamfer: a procedural method declares
        # organ structure even when (here, always) it is also recon-scored vs GT.
        om = db.execute(select(OrganMetric).where(OrganMetric.output_id == o.id)).scalars().first()
        if om is not None:
            organ_by_gen[o.generator_id].append(om)

    rows = []
    for gid, ms in by_gen.items():
        chamfers = [m.chamfer for m in ms]
        fscores = [m.fscore for m in ms if m.fscore is not None]
        covs = [m.coverage for m in ms if m.coverage is not None]
        best_m = min(ms, key=lambda m: m.chamfer)
        chamfer_mean = statistics.fmean(chamfers)
        band_hi = best_m.gt_band_hi  # P75 band — shared across a species' recons
        # Verdict on the TYPICAL recon (mean vs band P75), NOT the cherry-picked best —
        # else a high-variance method with one lucky recon reads PASS while a consistently
        # near-miss method reads FAIL, contradicting the mean ranking shown alongside.
        verdict = None
        if band_hi is not None:
            verdict = "PASS" if chamfer_mean <= band_hi else "FAIL"
        row = {
            "generator": _gen_name(db, gid),
            "n": len(chamfers),
            "chamfer_mean": round(chamfer_mean, 4),
            "chamfer_std": round(statistics.pstdev(chamfers), 4) if len(chamfers) >= 2 else None,
            "chamfer_best": round(min(chamfers), 4),
            "fscore_mean": round(statistics.fmean(fscores), 3) if fscores else None,
            "coverage_mean": round(statistics.fmean(covs), 4) if covs else None,
            "species_verdict": verdict,
            "gt_band": [best_m.gt_band_lo, best_m.gt_band_hi],
        }
        row.update(_organ_summary(organ_by_gen.get(gid, [])))
        rows.append(row)
    rows.sort(key=lambda r: r["chamfer_mean"])
    return rows


def _organ_summary(oms: list[OrganMetric]) -> dict:
    """Collapse a method's OrganMetric rows into the board's organ-fidelity cell.

    `organ_fidelity` = mean of scored fidelities (None when the method is N/A on this axis —
    no structure-known rows — so the board renders "—"). An honest 0.0 (structural gap) is a
    valid finding and IS averaged in. `organ_note` surfaces the un-referenced-species / not-
    modeled caveat for the explain-row; `organ_attributes` is one representative per-attribute
    detail (identical across a method's same-species recons)."""
    import json
    import statistics

    scored = [
        o.botanical_fidelity
        for o in oms
        if o.status == "scored" and o.botanical_fidelity is not None
    ]
    no_ref = [o for o in oms if o.status == "no_reference"]
    if scored:
        attrs_src = next((o for o in oms if o.status == "scored"), None)
        fidelity = round(statistics.fmean(scored), 3)
        note = ""
    elif no_ref:
        attrs_src = no_ref[0]
        fidelity = None
        note = no_ref[0].note or "no botanical reference"
    else:
        # No structure-known rows for this method → N/A on the organ axis.
        return {"organ_fidelity": None, "organ_note": "", "organ_attributes": {}}
    try:
        attributes = json.loads(attrs_src.attributes) if attrs_src else {}
    except (ValueError, TypeError):
        attributes = {}
    return {"organ_fidelity": fidelity, "organ_note": note, "organ_attributes": attributes}


def recon_confounds(db: Session, task_id: int) -> dict | None:
    """The pinned scoring confounds for a task's recons (criterion-2 reproducibility), or None.

    Shared across the task's metrics (the leaderboard pins them) — return the first ok one.
    """
    outs = (
        db.execute(
            select(ModelOutput).where(
                ModelOutput.task_id == task_id, ModelOutput.is_gold.is_(False)
            )
        )
        .scalars()
        .all()
    )
    for o in outs:
        m = db.execute(select(Metric).where(Metric.output_id == o.id)).scalars().first()
        if m is not None and m.status == "ok":
            return {
                "point_count": m.point_count,
                "tau": m.tau,
                "scorer_version": m.scorer_version,
                "gt_version_hash": m.gt_version_hash,
            }
    return None


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
    from .models import Criterion, Rating, Task

    task = db.get(Task, task_id)
    cat_id = task.category_id if task else None
    # Mode-A votes feed the 'overall' criterion (the arena default). Scope the BT lookup
    # to it so vote_rank is well-defined even when other criteria also have ratings.
    overall = db.execute(select(Criterion).where(Criterion.slug == "overall")).scalars().first()
    overall_id = overall.id if overall else None
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
                        Rating.criterion_id == overall_id,
                        (Rating.category_id == cat_id) | (Rating.category_id.is_(None)),
                    )
                    .order_by(Rating.category_id.is_(None))  # prefer category-scoped over global
                )
                .scalars()
                .first()
            )
            # Only count a BT rank if the generator actually has votes — a Rating row
            # exists at default bt_score=1000 before any game, which would otherwise
            # fake an agreement signal. n_games>0 = real Mode-A data.
            voted = rating is not None and rating.n_games > 0
            best[gid] = {
                "generator_id": gid,
                "generator": _gen_name(db, gid),
                "chamfer": m.chamfer,
                "bt": rating.bt_score if voted else None,
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

    # Status states for the real-data shape (the page renders the right message):
    #   empty        — nothing scored yet (run /admin/rescore)
    #   insufficient — <2 methods scored (can't correlate)
    #   no_votes     — methods scored but Mode-A votes not in yet (the common early state)
    #   ok           — both signals present; spearman meaningful
    if not entries:
        status = "empty"
    elif len(entries) < 2:
        status = "insufficient"
    elif len(common) < 2:
        status = "no_votes"
    else:
        status = "ok"

    rows = []
    for e in by_chamfer:
        mr = metric_rank[e["generator_id"]]
        vr = vote_rank.get(e["generator_id"])
        rows.append(
            {
                "generator": e["generator"],
                "metric_rank": mr,
                "vote_rank": vr,
                "disagree": vr is not None and mr != vr,  # metric and votes rank it differently
            }
        )
    return {
        "status": status,
        "spearman": sp,
        "n": len(common),
        "n_methods": len(entries),
        "rows": rows,
    }


def cross_species_summary(db: Session) -> list[dict]:
    """One-row-per-ReconTask summary for the /benchmark cross-species agreement table.

    Each row: task_id, species (task title), spearman, n_common (methods both voted &
    scored), n_methods, status. Negative spearman = perception inverts geometric fidelity.
    Pure read; never modifies state.
    """
    from .models import ReconTask, Task

    recon_tasks = db.execute(select(ReconTask)).scalars().all()
    rows = []
    for rt in recon_tasks:
        task = db.get(Task, rt.task_id)
        if task is None:
            continue
        agr = agreement(db, rt.task_id)
        rows.append(
            {
                "task_id": rt.task_id,
                "species": task.title,
                "spearman": agr.get("spearman"),
                "n_common": agr.get("n"),
                "n_methods": agr.get("n_methods"),
                "status": agr.get("status"),
            }
        )
    return rows
