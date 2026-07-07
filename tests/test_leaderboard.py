"""Tests for the leaderboard credibility surface: CI-grouped rank + CI bar."""

from __future__ import annotations

from app.main import _leaderboard_rows
from app.database import SessionLocal
from app.seed import seed_all


def setup_module(_module):
    seed_all(force=True)


def test_leaderboard_rows_have_ci_bar_geometry():
    with SessionLocal() as db:
        rows = _leaderboard_rows(db, "overall", None)
    assert rows, "expected seeded generators on the global overall board"
    for r in rows:
        assert 0.0 <= r["ci_left"] <= 100.0
        assert 0.0 <= r["ci_width"] <= 100.0
        assert "rank" in r


def test_ci_grouped_rank_matches_formula():
    # Directly exercise the rank rule against the serialized rows.
    from app.ranking import rank_by_ci

    with SessionLocal() as db:
        rows = _leaderboard_rows(db, "overall", None)
    expected = rank_by_ci([(r["bt_lower"], r["bt_upper"]) for r in rows])
    assert [r["rank"] for r in rows] == expected
    # Rank 1 always exists; ranks are non-decreasing down the (point-sorted) board.
    assert rows[0]["rank"] == 1
    assert all(rows[i]["rank"] <= rows[i + 1]["rank"] for i in range(len(rows) - 1))


def test_leaderboard_page_renders_rank_ub_and_ci_bar():
    from fastapi.testclient import TestClient
    from app.main import app

    html = TestClient(app).get("/leaderboard").text
    # Design-parity pass (task-lb) renamed the visible header from "Rank (UB)" to a plain
    # "Rank" (the prototype's compact column label); the CI-grouped-rank SEMANTICS are
    # unchanged (still `lb-th-rank` with the overlapping-CI tooltip) so check structure, not
    # the exact retired copy.
    assert "lb-th-rank" in html
    assert "ci-bar" in html  # the whisker bar is present


def test_tie_is_split_into_both_directions_for_bt():
    """A 'tie' vote must feed Bradley-Terry as one win in EACH direction (not dropped)."""
    from app import service
    from app.models import Comparison, Criterion, ModelOutput, Vote

    with SessionLocal() as db:
        db.query(Vote).delete()
        db.query(Comparison).delete()
        db.commit()
        crit = db.query(Criterion).filter_by(slug="overall").one()
        a = db.query(ModelOutput).filter_by(is_gold=False).first()
        b = (
            db.query(ModelOutput)
            .filter(ModelOutput.task_id == a.task_id, ModelOutput.id != a.id, ~ModelOutput.is_gold)
            .first()
        )
        comp = Comparison(
            task_id=a.task_id,
            output_a_id=a.id,
            output_b_id=b.id,
            criterion_id=crit.id,
            session_id="tie-sess",
        )
        db.add(comp)
        db.flush()
        db.add(Vote(comparison_id=comp.id, winner="tie", session_id="tie-sess"))
        db.commit()

        matches, _groups = service._matches_for_scope(db, crit.id, None)
        ga, gb = a.generator_id, b.generator_id
    # The single tie contributes BOTH orderings -- split credit, not dropped.
    assert (ga, gb) in matches
    assert (gb, ga) in matches


def test_bad_vote_excluded_from_matches():
    from app import service
    from app.models import Comparison, Criterion, ModelOutput, Vote

    with SessionLocal() as db:
        db.query(Vote).delete()
        db.query(Comparison).delete()
        db.commit()
        crit = db.query(Criterion).filter_by(slug="overall").one()
        a = db.query(ModelOutput).filter_by(is_gold=False).first()
        b = (
            db.query(ModelOutput)
            .filter(ModelOutput.task_id == a.task_id, ModelOutput.id != a.id, ~ModelOutput.is_gold)
            .first()
        )
        comp = Comparison(
            task_id=a.task_id,
            output_a_id=a.id,
            output_b_id=b.id,
            criterion_id=crit.id,
            session_id="bad-sess",
        )
        db.add(comp)
        db.flush()
        db.add(Vote(comparison_id=comp.id, winner="bad", session_id="bad-sess"))
        db.commit()
        matches, _groups = service._matches_for_scope(db, crit.id, None)
    assert matches == []  # 'bad' contributes nothing


def test_methodology_mentions_rank_ub_and_ties():
    from fastapi.testclient import TestClient
    from app.main import app

    html = TestClient(app).get("/methodology").text
    assert "Rank (UB)" in html
    assert "tie" in html.lower()


# --------------------------------------------------------------- design-parity (task-lb)


def test_overall_tab_merges_paradigms_into_one_flat_bt_ranking():
    """The 'Overall' tab (no paradigm filter) DELIBERATELY merges every paradigm into one
    BT-desc ranking (design-parity task-lb) instead of the old per-paradigm-group rank. With
    4 generators across 2 non-overlapping-CI paradigms, each strictly outscoring the next,
    their ranks must come out STRICTLY increasing in score order -- e.g. the 2nd-best overall
    (a DIFFERENT paradigm than the best) must NOT tie the best generator's rank the way the
    old per-paradigm-group behavior would (each paradigm's own top row got rank=1). The DB may
    carry other generators/ratings from earlier tests in this shared session, so this checks
    strict relative ordering among the 4 fixture rows rather than asserting exact 1..4 (which
    would be fragile against that unrelated pollution)."""
    import uuid

    from app import main as arena_main
    from app.models import Criterion, Generator, Rating

    with SessionLocal() as db:
        crit = db.query(Criterion).filter_by(slug="overall").first() or Criterion(
            slug="overall", name="Overall"
        )
        if crit.id is None:
            db.add(crit)
            db.commit()
        slugs = []
        # 4 distinct, non-overlapping bt scores spread across 2 paradigms.
        for pgm, bt in (
            ("tlb-a", 2000.0),
            ("tlb-b", 1500.0),
            ("tlb-a", 1000.0),
            ("tlb-b", 500.0),
        ):
            slug = f"tlbtest-{pgm}-{uuid.uuid4().hex}"
            g = Generator(slug=slug, name=slug, kind="model", paradigm=pgm)
            db.add(g)
            db.flush()
            slugs.append(slug)
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
        rows_by_slug = {r["slug"]: r for r in rows}
        ranks = [rows_by_slug[s]["rank"] for s in slugs]
    assert ranks[0] < ranks[1] < ranks[2] < ranks[3], (
        f"expected strictly increasing ranks across the merged (cross-paradigm) board, got {ranks}"
    )


def test_generator_trend_series_reflects_real_votes_not_fabricated():
    """generator_trend_series must derive win-rate from REAL Vote.created timestamps: a
    generator that always wins its decisive votes gets an all-1.0 series; one with too few
    votes (<4) gets [] (a flat baseline), never a fabricated interpolation."""
    import uuid

    from app import service
    from app.models import Category, Comparison, Criterion, Generator, ModelOutput, Task, Vote

    with SessionLocal() as db:
        crit = db.query(Criterion).filter_by(slug="overall").first() or Criterion(
            slug="overall", name="Overall"
        )
        if crit.id is None:
            db.add(crit)
            db.commit()
        cat = Category(slug=f"trend-cat-{uuid.uuid4().hex[:8]}", name="Trend cat")
        db.add(cat)
        db.flush()
        task = Task(category_id=cat.id, title="trend-task", prompt="p")
        db.add(task)
        db.flush()
        winner = Generator(slug=f"trend-win-{uuid.uuid4().hex[:8]}", name="Winner")
        loser = Generator(slug=f"trend-lose-{uuid.uuid4().hex[:8]}", name="Loser")
        sparse = Generator(slug=f"trend-sparse-{uuid.uuid4().hex[:8]}", name="Sparse")
        db.add_all([winner, loser, sparse])
        db.flush()
        w_out = ModelOutput(task_id=task.id, generator_id=winner.id, asset_path="t/w.glb")
        l_out = ModelOutput(task_id=task.id, generator_id=loser.id, asset_path="t/l.glb")
        s_out = ModelOutput(task_id=task.id, generator_id=sparse.id, asset_path="t/s.glb")
        db.add_all([w_out, l_out, s_out])
        db.flush()
        for i in range(6):  # winner always beats loser -> a real, non-fabricated 1.0 win-rate
            comp = Comparison(
                task_id=task.id,
                output_a_id=w_out.id,
                output_b_id=l_out.id,
                criterion_id=crit.id,
                session_id=f"trend-sess-{i}",
            )
            db.add(comp)
            db.flush()
            db.add(Vote(comparison_id=comp.id, winner="a", session_id=f"trend-sess-{i}"))
        # sparse generator gets only 1 decisive vote (below the <4-vote sparsity floor).
        comp2 = Comparison(
            task_id=task.id,
            output_a_id=s_out.id,
            output_b_id=l_out.id,
            criterion_id=crit.id,
            session_id="trend-sess-sparse",
        )
        db.add(comp2)
        db.flush()
        db.add(Vote(comparison_id=comp2.id, winner="a", session_id="trend-sess-sparse"))
        db.commit()

        series = service.generator_trend_series(db, crit.id, category_ids={cat.id})
    assert winner.id in series
    populated = [v for v in series[winner.id] if v is not None]
    assert populated and all(v == 1.0 for v in populated)
    assert series.get(sparse.id, []) == []  # too sparse -> flat baseline, not fabricated


def test_enrich_leaderboard_rows_avatar_provenance_momentum():
    """Pure presentation helpers: avatar initials/hue, the paradigm-based provenance
    heuristic, and momentum derived from a trend series (never a rank-vs-last-period claim)."""
    from app.main import _avatar_initials, _enrich_leaderboard_rows, _momentum

    assert _avatar_initials("Radim/gaussian (full)") == "RG"
    assert _avatar_initials("google/gemini-2.5-pro (agentic)") == "GG"
    assert _avatar_initials("solo") == "SO"

    assert _momentum([0.3, 0.3, 0.5]) == "up"
    assert _momentum([0.5, 0.3, 0.3]) == "down"
    assert _momentum([0.4, 0.41]) == "flat"
    assert _momentum([]) == "flat"

    rows = [
        {
            "generator": "TRELLIS (Replicate)",
            "slug": "trellis",
            "paradigm": "image_recon",
            "kind": "model",
            "generator_id": 1,
            "n_games": 5,
        },
        {
            "generator": "ROMI Arabidopsis scan",
            "slug": "romi",
            "paradigm": "capture_scan",
            "kind": "model",
            "generator_id": 2,
            "n_games": 100,
        },
    ]
    out = _enrich_leaderboard_rows(rows, trend_by_gid={1: [0.2, 0.2, 0.6]})
    trellis, romi = out
    assert trellis["avatar"] == "TR"
    assert 0 <= trellis["avatar_hue"] < 360
    assert trellis["provenance"] == ["paper", "code"]
    assert trellis["momentum"] == "up"
    assert trellis["detail_url"] == "/models/trellis"
    assert trellis["provisional"] is True  # 5 < FIRM_VOTE_THRESHOLD
    assert romi["provenance"] == ["data"]
    assert romi["trend"] == []  # no trend series supplied for this generator_id
    assert romi["provisional"] is False  # 100 >= FIRM_VOTE_THRESHOLD
