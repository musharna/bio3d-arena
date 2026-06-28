# app/thumbnails.py
"""Persist rendered card thumbnails (PNG) for ModelOutputs.

The Playwright capture lives in scripts/render_thumbnails.py; this module is the testable
storage half: save the PNG under renders/<output_id>.png and upsert the output's Critique row's
render_path (preserving any existing critic_note/dists). The spotlight reads Critique.render_path
to show a real preview instead of a 'click to view' placeholder.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Critique, ModelOutput
from .storage import StorageBackend, get_storage


def thumbnail_rel_path(output_id: int) -> str:
    """Storage key for an output's rendered thumbnail PNG."""
    return f"renders/{output_id}.png"


def store_thumbnail(
    db: Session,
    output: ModelOutput,
    png: bytes,
    *,
    storage: StorageBackend | None = None,
) -> str:
    """Save the PNG to storage and upsert Critique.render_path for the output. Returns the rel path.

    Upserts by output_id (the Critique row is one-per-output); an existing row keeps its
    critic_note / dists / dreamsim and only its render_path + status are updated.
    """
    store = storage if storage is not None else get_storage()
    rel = thumbnail_rel_path(output.id)
    store.save(rel, png)
    crit = db.execute(select(Critique).where(Critique.output_id == output.id)).scalars().first()
    if crit is None:
        crit = Critique(output_id=output.id)
        db.add(crit)
    crit.render_path = rel
    crit.status = "ok"
    db.flush()
    return rel
