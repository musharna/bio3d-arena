from __future__ import annotations

from app.database import SessionLocal, init_db
from app.models import Generator


def setup_module(_m):
    init_db()


def test_generator_paradigm_defaults_empty_and_persists():
    with SessionLocal() as db:
        g = Generator(slug="pgm-test-gen", name="t", kind="model")
        db.add(g)
        db.commit()
        assert g.paradigm == ""  # default
        g.paradigm = "image_recon"
        db.commit()
        got = db.query(Generator).filter_by(slug="pgm-test-gen").one()
        assert got.paradigm == "image_recon"
        db.delete(got)
        db.commit()
