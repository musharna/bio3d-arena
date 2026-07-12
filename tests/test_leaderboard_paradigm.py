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


def test_empty_string_paradigm_means_no_filter():
    """C1 — the /leaderboard route binds an unset `?paradigm=` query param to "", not None.
    `_leaderboard_rows` must treat "" the same as None (unfiltered), not as an active filter
    that matches zero generators."""
    with SessionLocal() as db:
        crit = db.query(Criterion).filter_by(slug="overall").first() or Criterion(
            slug="overall", name="Overall"
        )
        if crit.id is None:
            db.add(crit)
            db.commit()
        names_by_paradigm = {}
        for pgm in ("image_recon", "procedural_llm"):
            slug = f"c1test-{pgm}-{uuid.uuid4().hex}"
            g = Generator(slug=slug, name=slug, kind="model", paradigm=pgm)
            db.add(g)
            db.flush()
            names_by_paradigm[pgm] = slug
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

        empty_filter_rows = arena_main._leaderboard_rows(db, "overall", None, paradigm="")
        empty_filter_names = {r["generator"] for r in empty_filter_rows}
        assert names_by_paradigm["image_recon"] in empty_filter_names
        assert names_by_paradigm["procedural_llm"] in empty_filter_names

        scoped_rows = arena_main._leaderboard_rows(db, "overall", None, paradigm="procedural_llm")
        scoped_names = {r["generator"] for r in scoped_rows}
        assert names_by_paradigm["procedural_llm"] in scoped_names
        assert names_by_paradigm["image_recon"] not in scoped_names


def test_no_filter_leaderboard_ranks_within_each_paradigm():
    """I1 — with no paradigm filter, rank must be computed PER paradigm group, not
    globally across paradigms (cross-paradigm BT comparisons are the confound the project
    removes). Two generators per paradigm, non-overlapping CIs within each group, so each
    paradigm's better generator should land at rank 1 within its own group."""
    with SessionLocal() as db:
        crit = db.query(Criterion).filter_by(slug="overall").first() or Criterion(
            slug="overall", name="Overall"
        )
        if crit.id is None:
            db.add(crit)
            db.commit()
        by_paradigm: dict[str, list[str]] = {"i1test-a": [], "i1test-b": []}
        for pgm in by_paradigm:
            for bt in (2000.0, 1000.0):  # deliberately non-overlapping CIs
                slug = f"{pgm}-{uuid.uuid4().hex}"
                g = Generator(slug=slug, name=slug, kind="model", paradigm=pgm)
                db.add(g)
                db.flush()
                by_paradigm[pgm].append(slug)
                db.add(
                    Rating(
                        generator_id=g.id,
                        criterion_id=crit.id,
                        category_id=None,
                        bt_score=bt,
                        bt_lower=bt - 5.0,
                        bt_upper=bt + 5.0,
                        n_games=5,
                    )
                )
        db.commit()

        rows = arena_main._leaderboard_rows(db, "overall", None)
        rows_by_slug = {r["generator"]: r for r in rows}
        for pgm, slugs in by_paradigm.items():
            best_slug = slugs[0]  # bt_score=2000.0, seeded first
            worst_slug = slugs[1]  # bt_score=1000.0
            assert rows_by_slug[best_slug]["rank"] == 1, (
                f"{pgm} top generator should be rank 1 within its own paradigm group"
            )
            assert rows_by_slug[worst_slug]["rank"] > 1
