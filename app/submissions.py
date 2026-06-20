"""Community submission + moderation queue.

Authors submit a 3D output for a task; it is validated + stored immediately but
held as `pending`. An admin approves (→ a ModelOutput reusing the stored blob
enters the arena) or rejects (with a note). Keeps untrusted content out of the
rankings until a human signs off.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import ingest
from .models import ModelOutput, Submission, Task
from .storage import get_storage


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def create_submission(
    db: Session,
    task_id: int,
    generator_slug: str,
    data: bytes,
    ext: str = "glb",
    title: str = "",
    meta: dict | None = None,
    submitter: str = "",
    session_id: str = "",
    generator_name: str = "",
) -> Submission:
    """Validate + store a submitted asset and queue it for moderation."""
    task = db.get(Task, task_id)
    if task is None:
        raise ingest.IngestError(f"Unknown task_id {task_id}.")
    if not generator_slug:
        raise ingest.IngestError("generator_slug is required.")
    stats = ingest.validate_asset(data, ext)  # reject unparseable/empty up front
    rel = str(Path("submissions") / f"{uuid.uuid4().hex}.{ext.lower()}").replace("\\", "/")
    get_storage().save(rel, data)
    provenance = {"sha256": ingest.sha256(data), "submitted": True, **stats, **(meta or {})}
    sub = Submission(
        task_id=task_id,
        generator_slug=generator_slug,
        generator_name=generator_name or "",
        title=title,
        asset_path=rel,
        asset_format=ext.lower(),
        meta_json=json.dumps(provenance),
        submitter=submitter,
        session_id=session_id,
        status="pending",
    )
    db.add(sub)
    db.flush()
    return sub


def approve(db: Session, submission_id: int, note: str = "") -> ModelOutput:
    """Approve a pending submission → create a ModelOutput reusing the stored blob."""
    sub = db.get(Submission, submission_id)
    if sub is None:
        raise ingest.IngestError("Unknown submission.")
    if sub.status != "pending":
        raise ingest.IngestError(f"Submission already {sub.status}.")
    gen = ingest.upsert_generator(db, sub.generator_slug, name=sub.generator_name or None)
    task = db.get(Task, sub.task_id)
    output = ModelOutput(
        task_id=sub.task_id,
        generator_id=gen.id,
        title=sub.title or f"{task.title} — {gen.name}",
        asset_path=sub.asset_path,
        asset_format=sub.asset_format,
        meta_json=sub.meta_json,
    )
    db.add(output)
    db.flush()
    try:
        from . import validation_service

        validation_service.validate_and_store(db, output)
    except Exception:  # noqa: BLE001 — approval must not fail on a bad/odd asset
        pass
    sub.status = "approved"
    sub.review_note = note
    sub.model_output_id = output.id
    sub.reviewed_at = _utcnow()
    return output


def reject(db: Session, submission_id: int, note: str = "") -> Submission:
    sub = db.get(Submission, submission_id)
    if sub is None:
        raise ingest.IngestError("Unknown submission.")
    if sub.status != "pending":
        raise ingest.IngestError(f"Submission already {sub.status}.")
    sub.status = "rejected"
    sub.review_note = note
    sub.reviewed_at = _utcnow()
    return sub


def list_submissions(db: Session, status: str | None = None) -> list[Submission]:
    stmt = select(Submission).order_by(Submission.created.desc())
    if status:
        stmt = stmt.where(Submission.status == status)
    return list(db.execute(stmt).scalars().all())
