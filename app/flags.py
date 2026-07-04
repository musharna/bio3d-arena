# app/flags.py
"""Bad-output helpers: completeness-based pool exclusion + human flag recording."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Completeness, ModelOutput, OutputFlag, _utcnow


def excluded_output_ids_by_completeness(db: Session, categories: set[str]) -> set[int]:
    """Output ids whose completeness category is in `categories`. Empty categories → empty set.
    Completeness is one row per output (output_id is unique; rescore overwrites)."""
    if not categories:
        return set()
    return {
        oid
        for (oid,) in db.execute(
            select(Completeness.output_id).where(Completeness.category.in_(categories))
        ).all()
    }


def distinct_flag_count(db: Session, output_id: int) -> int:
    """Number of DISTINCT sessions that have flagged this output."""
    return int(
        db.execute(
            select(func.count(func.distinct(OutputFlag.session_id))).where(
                OutputFlag.output_id == output_id
            )
        ).scalar_one()
    )


def record_flag(
    db: Session, output_id: int, session_id: str, reason: str, threshold: int
) -> tuple[bool, int]:
    """Record one flag (idempotent per (output, session)); auto-hide at `threshold` distinct
    sessions. Returns (is_hidden, distinct_count). Caller commits."""
    existing = (
        db.execute(
            select(OutputFlag).where(
                OutputFlag.output_id == output_id, OutputFlag.session_id == session_id
            )
        )
        .scalars()
        .first()
    )
    if existing is None:
        db.add(OutputFlag(output_id=output_id, session_id=session_id, reason=reason))
        db.flush()
    count = distinct_flag_count(db, output_id)
    out = db.get(ModelOutput, output_id)
    if out is not None and out.hidden_at is None and count >= threshold:
        out.hidden_at = _utcnow()
    return (out is not None and out.hidden_at is not None), count
