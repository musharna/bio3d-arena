from __future__ import annotations

import uuid

from app import main as arena_main
from app import service
from app.database import SessionLocal, init_db
from app.models import Criterion, Generator, Rating


def setup_module(_m):
    init_db()


def test_leaderboard_rows_carry_and_filter_paradigm():
    with SessionLocal() as db:
        crit = db.query(Criterion).filter_by(slug="overall").first() or Criterion(
            slug="overall", name="Overall"
        )
        if crit.id is None:
            db.add(crit)
            db.commit()
        gen_ids = {}
        for pgm in ("image_recon", "procedural_llm"):
            g = Generator(
                slug=f"lbtest-{pgm}-{uuid.uuid4().hex}", name=pgm, kind="model", paradigm=pgm
            )
            db.add(g)
            db.flush()
            gen_ids[pgm] = g.id
            db.add(
                Rating(
                    generator_id=g.id,
                    criterion_id=crit.id,
                    category_id=None,
                    bt_score=1000.0,
                    bt_lower=990.0,
                    bt_upper=1010.0,
                    n_games=5,
                )
            )
        db.commit()
        rows = arena_main._leaderboard_rows(db, "overall", None)
        row_paradigms = {
            r["paradigm"]
            for r in rows
            if any(r.get("generator") == pgm for pgm in ("image_recon", "procedural_llm"))
        }
        assert "image_recon" in row_paradigms
        assert "procedural_llm" in row_paradigms
        only = arena_main._leaderboard_rows(db, "overall", None, paradigm="procedural_llm")
        assert all(r["paradigm"] == "procedural_llm" for r in only)
        assert not any(r["paradigm"] == "image_recon" for r in only)


def test_coverage_summary_has_by_paradigm():
    with SessionLocal() as db:
        cov = service.coverage_summary(db)
        assert "by_paradigm" in cov and isinstance(cov["by_paradigm"], dict)
