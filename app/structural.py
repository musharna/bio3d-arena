# app/structural.py
"""Structural-validity predicate: pure trimesh geometry, no VLM. Rejects ONLY unambiguous
degeneracy (conservative / precision-first) — a false positive silently removes a real candidate,
so thresholds are tuned to reject the flagged broken set with ZERO false positives on good meshes."""

from __future__ import annotations

import json
import os

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config
from .admissibility import Verdict
from .models import Admissibility, ModelOutput

VERSION = "structural-v1"

# Conservative floors. A real 3D plant mesh has thousands of verts/faces and true 3D extent;
# a degenerate output (single triangle, flat sheet, empty/corrupt) fails one of these.
MIN_VERTS = 8
MIN_FACES = 8
MIN_EXTENT_RATIO = 0.02  # smallest bbox extent / bbox diagonal; below this = a sliver/flat


def evaluate_glb(path: str) -> Verdict:
    """Load a GLB (concatenated to one mesh) and return an admissibility Verdict."""
    import trimesh  # local import: heavy

    try:
        mesh = trimesh.load(path, force="mesh")  # repo idiom (ingest._validate_mesh)
    except Exception as e:  # noqa: BLE001 — a corrupt asset is a reject, not a crash
        return Verdict(False, "unreadable", {"error": str(e)[:200]})

    verts = np.asarray(getattr(mesh, "vertices", np.empty((0, 3))), dtype=float)
    faces = getattr(mesh, "faces", None)
    nv = int(len(verts))
    nf = 0 if faces is None else int(len(faces))

    if nv == 0 or nf == 0:
        return Verdict(False, "empty", {"verts": nv, "faces": nf})
    if not np.isfinite(verts).all():
        return Verdict(False, "non_finite", {})
    if nv < MIN_VERTS or nf < MIN_FACES:
        return Verdict(False, "too_small", {"verts": nv, "faces": nf})

    extents = np.asarray(mesh.extents, dtype=float)  # bbox size (3,)
    diag = float(np.linalg.norm(extents))
    ratio = float(extents.min() / diag) if diag > 0 else 0.0
    if ratio < MIN_EXTENT_RATIO:
        return Verdict(False, "degenerate_bbox", {"extent_ratio": ratio})

    return Verdict(True, "", {"verts": nv, "faces": nf, "extent_ratio": ratio})


def upsert_verdict(db: Session, output_id: int, predicate: str, verdict: Verdict, version: str):
    """Insert or overwrite the single (output_id, predicate) admissibility row. Caller commits.
    Explicit flush (SessionLocal is autoflush=False) so a second upsert_verdict call in the same
    uncommitted transaction sees the first call's row instead of racing it into a duplicate."""
    row = db.query(Admissibility).filter_by(output_id=output_id, predicate=predicate).one_or_none()
    if row is None:
        row = Admissibility(output_id=output_id, predicate=predicate)
        db.add(row)
    row.admit = verdict.admit
    row.reason = verdict.reason
    row.detail_json = json.dumps(verdict.detail)
    row.version = version
    db.flush()
    return row


def enumerate_structural_work(db: Session) -> list[int]:
    """Output ids lacking a current-VERSION structural verdict (non-gold)."""
    have = {
        oid
        for (oid,) in db.execute(
            select(Admissibility.output_id).where(
                Admissibility.predicate == "structural", Admissibility.version == VERSION
            )
        ).all()
    }
    all_ids = [
        oid
        for (oid,) in db.execute(select(ModelOutput.id).where(ModelOutput.is_gold.is_(False))).all()
    ]
    return [oid for oid in all_ids if oid not in have]


def _asset_path(output: ModelOutput) -> str:
    return os.path.join(str(config.ASSET_DIR), output.asset_path)


def evaluate_outputs(db: Session, output_ids: list[int]) -> dict:
    """Evaluate each output's GLB and upsert its structural verdict. Fail-loud per output:
    a missing/unreadable asset yields a reject verdict (recorded), never aborts the batch.
    Caller commits."""
    scored = errors = 0
    seen: set[int] = set()
    for oid in output_ids:
        if oid in seen:
            continue
        seen.add(oid)
        out = db.get(ModelOutput, oid)
        if out is None:
            errors += 1
            continue
        try:
            verdict = evaluate_glb(_asset_path(out))
        except Exception as e:  # noqa: BLE001 — record a reject, keep going
            verdict = Verdict(False, "unreadable", {"error": str(e)[:200]})
            errors += 1
        upsert_verdict(db, oid, "structural", verdict, VERSION)
        scored += 1
    return {"scored": scored, "errors": errors}


class StructuralPredicate:
    name = "structural"
    version = VERSION

    def rejected_output_ids(self, db: Session) -> set[int]:
        return {
            oid
            for (oid,) in db.execute(
                select(Admissibility.output_id).where(
                    Admissibility.predicate == "structural", Admissibility.admit.is_(False)
                )
            ).all()
        }
