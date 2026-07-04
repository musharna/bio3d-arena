# tests/test_structural_ingest.py
from __future__ import annotations

import io
import uuid

import trimesh

from app import ingest
from app.database import SessionLocal, init_db
from app.models import Admissibility, Category, Task


def setup_module(_m):
    init_db()


def _glb_bytes(mesh):
    buf = io.BytesIO()
    mesh.export(buf, file_type="glb")
    return buf.getvalue()


def test_ingest_creates_structural_verdict():
    with SessionLocal() as db:
        cat = Category(slug=f"si-{uuid.uuid4().hex[:8]}", name="C")
        db.add(cat)
        db.flush()
        t = Task(category_id=cat.id, title=f"si-{uuid.uuid4().hex[:8]}", prompt="p")
        db.add(t)
        db.commit()
        # register_output(db, task_id, generator_slug, data, ext=...) upserts the generator itself
        # and creates the ModelOutput (app/ingest.py:172).
        out, created = ingest.register_output(
            db,
            task_id=t.id,
            generator_slug=f"si-{uuid.uuid4().hex}",
            data=_glb_bytes(trimesh.creation.box((1, 1, 1))),
            ext="glb",
        )
        db.commit()
        row = (
            db.query(Admissibility)
            .filter_by(output_id=out.id, predicate="structural")
            .one_or_none()
        )
        assert row is not None and row.admit is True
