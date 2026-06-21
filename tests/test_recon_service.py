"""Mode-B recon scoring service — fake-scorer unit tests.

Fakes mirror the LIVE §11 service envelope ({task_id, bundle_version, metrics:{...,params}}).
The real /score round-trip is exercised by the manual smoke against gt_bundle_smoke; these
inject a fake scorer so the mapping/aggregation logic is covered without the service.
"""

from __future__ import annotations

from sqlalchemy import select

from app import recon_service
from app.database import SessionLocal, init_db
from app.models import Category, Generator, Metric, ModelOutput, Task
from app.storage import get_storage


def setup_module(_module):
    init_db()
    # A stub GLB so score_and_store's storage read succeeds (the fake scorer ignores bytes).
    get_storage().save("seed/x.glb", b"glTF-stub-bytes")


def fake_card(chamfer: float = 0.013) -> dict:
    """A live-shaped /score response envelope with a tunable chamfer."""
    return {
        "task_id": "arabidopsis",
        "bundle_version": "sha256:cafe",
        "metrics": {
            "chamfer": chamfer,
            "fscore_at_tau": 0.79,
            "coverage": 0.71,
            "matched_gt_index": 1,
            "n_gt": 6,
            "height_cm": 12.0,
            "height_rel_error": None,
            "aspect_error": 0.1,
            "params": {
                "tau": 0.05,
                "seed": 0,
                "metric": "unit_bbox_chamfer",
                "n_points": 30000,
                "scale_cm": None,
            },
        },
        "band": {"band_median": 0.10, "band_p75": 0.14, "n_baseline": 5},
        "within_variation": True,
    }


def _mk_output(db, slug):
    cat = Category(slug=f"c-{slug}", name="Plants")
    db.add(cat)
    db.flush()
    task = Task(category_id=cat.id, title=f"t-{slug}", prompt="p")
    gen = Generator(slug=f"g-{slug}", name=f"M-{slug}")
    db.add_all([task, gen])
    db.flush()
    out = ModelOutput(
        task_id=task.id, generator_id=gen.id, asset_path="seed/x.glb", asset_format="glb"
    )
    db.add(out)
    db.flush()
    return out


def test_score_and_store_maps_contract_to_metric():
    db = SessionLocal()
    try:
        out = _mk_output(db, "ok")
        m = recon_service.score_and_store(db, out, scorer=lambda b, t: fake_card())
        assert m.status == "ok"
        assert m.chamfer == 0.013
        assert m.nearest_shape_distance == 0.013  # chamfer is the matched min distance
        assert m.nearest_gt_idx == 1  # matched_gt_index
        assert m.fscore == 0.79
        assert m.tau == 0.05
        assert m.point_count == 30000  # params.n_points
        assert m.icp_seed == 0  # params.seed
        assert m.scorer_version == "unit_bbox_chamfer"  # params.metric
        assert m.gt_version_hash == "sha256:cafe"  # bundle_version (D2 pin)
        # GT natural-variation band + within-variation verdict (live channel)
        assert m.gt_band_lo == 0.10 and m.gt_band_hi == 0.14  # band_median / band_p75
        assert m.species_verdict == "PASS"  # within_variation True → PASS
    finally:
        db.close()


def test_score_and_store_upserts_not_duplicates():
    db = SessionLocal()
    try:
        out = _mk_output(db, "upsert")
        recon_service.score_and_store(db, out, scorer=lambda b, t: fake_card(chamfer=0.1))
        recon_service.score_and_store(db, out, scorer=lambda b, t: fake_card(chamfer=0.02))
        db.commit()
        rows = db.query(Metric).filter(Metric.output_id == out.id).all()
        assert len(rows) == 1
        assert rows[0].chamfer == 0.02  # latest wins
    finally:
        db.close()


def test_scorer_failure_records_error_not_crash():
    db = SessionLocal()
    try:
        out = _mk_output(db, "err")

        def boom(b, t):
            raise RuntimeError("scorer down")

        m = recon_service.score_and_store(db, out, scorer=boom)
        assert m.status == "error"
        assert "scorer down" in m.detail
    finally:
        db.close()


def test_rescore_all_skips_non_glb():
    db = SessionLocal()
    try:
        out = _mk_output(db, "pdb")
        out.asset_format = "pdb"
        db.flush()
        detail = recon_service.rescore_all(db, scorer=lambda b, t: fake_card())
        assert detail["skipped"] >= 1
    finally:
        db.close()


def test_admin_rescore_requires_token():
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    assert client.post("/admin/rescore", data={"token": "wrong"}).status_code == 401


def test_recon_method_leaderboard_aggregates_with_variance():
    # N recons for the SAME method (median-of-N expansion) collapse to one row with
    # n + mean ± std chamfer — error bars, not N point-estimate rows.
    db = SessionLocal()
    try:
        out = _mk_output(db, "agg")
        out_b = ModelOutput(
            task_id=out.task_id,
            generator_id=out.generator_id,  # SAME method, 2nd recon
            asset_path="seed/x.glb",
            asset_format="glb",
        )
        db.add(out_b)
        db.flush()
        recon_service.score_and_store(db, out, scorer=lambda b, t: fake_card(chamfer=0.04))
        recon_service.score_and_store(db, out_b, scorer=lambda b, t: fake_card(chamfer=0.06))
        db.commit()
        board = recon_service.recon_method_leaderboard(db, out.task_id)
        assert len(board) == 1  # one method, not two rows
        row = board[0]
        assert row["n"] == 2
        assert abs(row["chamfer_mean"] - 0.05) < 1e-9
        assert row["chamfer_std"] is not None and row["chamfer_std"] > 0
        assert abs(row["chamfer_best"] - 0.04) < 1e-9
    finally:
        db.close()


def test_method_verdict_is_mean_based_not_best():
    # fake_card band_p75 = 0.14. Two recons 0.13 & 0.17 → mean 0.15 (> band) FAIL, even
    # though the BEST (0.13) is within the band. Verdict must follow the typical recon.
    db = SessionLocal()
    try:
        out = _mk_output(db, "vmean")
        out_b = ModelOutput(
            task_id=out.task_id,
            generator_id=out.generator_id,
            asset_path="seed/x.glb",
            asset_format="glb",
        )
        db.add(out_b)
        db.flush()
        recon_service.score_and_store(db, out, scorer=lambda b, t: fake_card(chamfer=0.13))
        recon_service.score_and_store(db, out_b, scorer=lambda b, t: fake_card(chamfer=0.17))
        db.commit()
        row = recon_service.recon_method_leaderboard(db, out.task_id)[0]
        assert row["chamfer_best"] == 0.13  # best is within band 0.14
        assert row["species_verdict"] == "FAIL"  # but the mean (0.15) is not → FAIL
    finally:
        db.close()


def test_recon_method_leaderboard_single_recon_no_std():
    db = SessionLocal()
    try:
        out = _mk_output(db, "agg1")
        recon_service.score_and_store(db, out, scorer=lambda b, t: fake_card(chamfer=0.07))
        db.commit()
        board = recon_service.recon_method_leaderboard(db, out.task_id)
        assert board[0]["n"] == 1
        assert board[0]["chamfer_std"] is None  # no spread from a single recon
    finally:
        db.close()


def test_recon_leaderboard_sorts_by_chamfer():
    db = SessionLocal()
    try:
        out = _mk_output(db, "lb")
        gen2 = Generator(slug="g-lb2", name="M2")
        db.add(gen2)
        db.flush()
        out2 = ModelOutput(
            task_id=out.task_id, generator_id=gen2.id, asset_path="seed/x.glb", asset_format="glb"
        )
        db.add(out2)
        db.flush()
        recon_service.score_and_store(db, out, scorer=lambda b, t: fake_card(chamfer=0.05))
        recon_service.score_and_store(db, out2, scorer=lambda b, t: fake_card(chamfer=0.01))
        db.commit()
        board = recon_service.recon_leaderboard(db, out.task_id)
        assert [r["chamfer"] for r in board] == [0.01, 0.05]  # best (lowest) first
    finally:
        db.close()


def test_agreement_returns_spearman_and_rows():
    db = SessionLocal()
    try:
        out = _mk_output(db, "agr")
        recon_service.score_and_store(db, out, scorer=lambda b, t: fake_card())
        db.commit()
        agr = recon_service.agreement(db, out.task_id)
        assert "spearman" in agr
        assert isinstance(agr["rows"], list)
    finally:
        db.close()


def test_agreement_status_insufficient_one_method():
    db = SessionLocal()
    try:
        out = _mk_output(db, "ins")
        recon_service.score_and_store(db, out, scorer=lambda b, t: fake_card())
        db.commit()
        agr = recon_service.agreement(db, out.task_id)
        assert agr["status"] == "insufficient"  # only 1 method scored
    finally:
        db.close()


def test_agreement_status_no_votes_with_two_methods():
    # Two scored methods but NO Mode-A votes (no Rating with n_games>0) → no_votes,
    # not a fake "ok". Rows present with vote_rank None.
    db = SessionLocal()
    try:
        out = _mk_output(db, "nv")
        gen2 = Generator(slug="g-nv2", name="M-nv2")
        db.add(gen2)
        db.flush()
        out2 = ModelOutput(
            task_id=out.task_id, generator_id=gen2.id, asset_path="seed/x.glb", asset_format="glb"
        )
        db.add(out2)
        db.flush()
        recon_service.score_and_store(db, out, scorer=lambda b, t: fake_card(chamfer=0.03))
        recon_service.score_and_store(db, out2, scorer=lambda b, t: fake_card(chamfer=0.06))
        db.commit()
        agr = recon_service.agreement(db, out.task_id)
        assert agr["status"] == "no_votes"
        assert agr["n_methods"] == 2
        assert all(r["vote_rank"] is None for r in agr["rows"])
    finally:
        db.close()


def test_agreement_uses_overall_criterion_bt_not_other_criteria():
    # Mode-A votes feed the 'overall' criterion. agreement() must read the OVERALL BT
    # rank, not whatever criterion .first() happens to return.
    from app.models import Category, Criterion, Rating

    db = SessionLocal()
    try:
        cat = Category(slug="c-crit", name="Plants")
        db.add(cat)
        # agreement() resolves the 'overall' criterion by slug — get-or-create it.
        overall = db.execute(select(Criterion).where(Criterion.slug == "overall")).scalars().first()
        if overall is None:
            overall = Criterion(slug="overall", name="Overall")
            db.add(overall)
        other = Criterion(slug="other-crit", name="Other")
        db.add(other)
        db.flush()
        task = Task(category_id=cat.id, title="t-crit", prompt="p")
        ga = Generator(slug="ga-crit", name="GA")
        gb = Generator(slug="gb-crit", name="GB")
        db.add_all([task, ga, gb])
        db.flush()
        for g, ch in ((ga, 0.02), (gb, 0.08)):  # GA better by metric (lower chamfer)
            o = ModelOutput(
                task_id=task.id, generator_id=g.id, asset_path="seed/x.glb", asset_format="glb"
            )
            db.add(o)
            db.flush()
            recon_service.score_and_store(
                db, o, scorer=(lambda c: lambda b, t: fake_card(chamfer=c))(ch)
            )
        # OVERALL: GA ahead (1100) — agrees with metric. OTHER criterion: reversed, to
        # prove agreement ignores it.
        db.add_all(
            [
                Rating(
                    generator_id=ga.id,
                    category_id=cat.id,
                    criterion_id=overall.id,
                    bt_score=1100,
                    n_games=5,
                ),
                Rating(
                    generator_id=gb.id,
                    category_id=cat.id,
                    criterion_id=overall.id,
                    bt_score=1000,
                    n_games=5,
                ),
                Rating(
                    generator_id=ga.id,
                    category_id=cat.id,
                    criterion_id=other.id,
                    bt_score=900,
                    n_games=5,
                ),
                Rating(
                    generator_id=gb.id,
                    category_id=cat.id,
                    criterion_id=other.id,
                    bt_score=1200,
                    n_games=5,
                ),
            ]
        )
        db.commit()
        agr = recon_service.agreement(db, task.id)
        assert agr["status"] == "ok"
        ranks = {r["generator"]: r["vote_rank"] for r in agr["rows"]}
        assert ranks["GA"] == 1 and ranks["GB"] == 2  # from OVERALL, not the reversed 'other'
        assert agr["spearman"] == 1.0  # metric and overall-vote fully agree
    finally:
        db.close()


def test_agreement_dedups_multiple_outputs_per_generator():
    # A generator with TWO outputs on one task must appear exactly once in the
    # agreement rows (collapsed to its best chamfer) — not double-counted.
    db = SessionLocal()
    try:
        out = _mk_output(db, "dup")
        out2 = ModelOutput(
            task_id=out.task_id,
            generator_id=out.generator_id,  # SAME generator, second output
            asset_path="seed/x.glb",
            asset_format="glb",
        )
        db.add(out2)
        db.flush()
        recon_service.score_and_store(db, out, scorer=lambda b, t: fake_card(chamfer=0.09))
        recon_service.score_and_store(db, out2, scorer=lambda b, t: fake_card(chamfer=0.02))
        db.commit()
        agr = recon_service.agreement(db, out.task_id)
        names = [r["generator"] for r in agr["rows"]]
        assert len(names) == len(set(names))  # each generator at most once
        gen_rows = [r for r in agr["rows"] if r["generator"] == "M-dup"]
        assert len(gen_rows) == 1
    finally:
        db.close()
