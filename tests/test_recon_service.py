"""Mode-B recon scoring service — fake-scorer unit tests.

The live /score round-trip against AgriGen's microservice is a DEFERRED real-execution
gate (needs §7A A2); these tests inject a fake scorer so the scaffold is exercised
without the service.
"""

from __future__ import annotations

from app import recon_service
from app.database import SessionLocal, init_db
from app.models import Category, Generator, Metric, ModelOutput, Task
from app.storage import get_storage


def setup_module(_module):
    init_db()
    # A stub GLB so score_and_store's storage read succeeds (the fake scorer ignores bytes).
    get_storage().save("seed/x.glb", b"glTF-stub-bytes")


FAKE_CARD = {
    "chamfer": 0.013,
    "nearest_shape_distance": 0.013,
    "nearest_gt_idx": 1,
    "fscore_at_tau": 0.79,
    "tau": 0.01,
    "coverage": 0.71,
    "species_verdict": "PASS",
    "gt_band": {"lo": 0.009, "hi": 0.021},
    "confounds": {
        "point_count": 16384,
        "icp_seed": 0,
        "scorer_version": "fake@1",
        "gt_version_hash": "sha256:cafe",
    },
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
        m = recon_service.score_and_store(db, out, scorer=lambda b, t: FAKE_CARD)
        assert m.status == "ok"
        assert m.chamfer == 0.013
        assert m.fscore == 0.79
        assert m.point_count == 16384
        assert m.gt_version_hash == "sha256:cafe"
        assert m.gt_band_lo == 0.009 and m.gt_band_hi == 0.021
    finally:
        db.close()


def test_score_and_store_upserts_not_duplicates():
    db = SessionLocal()
    try:
        out = _mk_output(db, "upsert")
        recon_service.score_and_store(db, out, scorer=lambda b, t: {**FAKE_CARD, "chamfer": 0.1})
        recon_service.score_and_store(db, out, scorer=lambda b, t: {**FAKE_CARD, "chamfer": 0.02})
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
        detail = recon_service.rescore_all(db, scorer=lambda b, t: FAKE_CARD)
        assert detail["skipped"] >= 1
    finally:
        db.close()


def test_admin_rescore_requires_token():
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    assert client.post("/admin/rescore", data={"token": "wrong"}).status_code == 401


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
        recon_service.score_and_store(db, out, scorer=lambda b, t: {**FAKE_CARD, "chamfer": 0.05})
        recon_service.score_and_store(db, out2, scorer=lambda b, t: {**FAKE_CARD, "chamfer": 0.01})
        db.commit()
        board = recon_service.recon_leaderboard(db, out.task_id)
        assert [r["chamfer"] for r in board] == [0.01, 0.05]  # best (lowest) first
    finally:
        db.close()


def test_agreement_returns_spearman_and_rows():
    db = SessionLocal()
    try:
        out = _mk_output(db, "agr")
        recon_service.score_and_store(db, out, scorer=lambda b, t: FAKE_CARD)
        db.commit()
        agr = recon_service.agreement(db, out.task_id)
        assert "spearman" in agr
        assert isinstance(agr["rows"], list)
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
        recon_service.score_and_store(db, out, scorer=lambda b, t: {**FAKE_CARD, "chamfer": 0.09})
        recon_service.score_and_store(db, out2, scorer=lambda b, t: {**FAKE_CARD, "chamfer": 0.02})
        db.commit()
        agr = recon_service.agreement(db, out.task_id)
        names = [r["generator"] for r in agr["rows"]]
        assert len(names) == len(set(names))  # each generator at most once
        gen_rows = [r for r in agr["rows"] if r["generator"] == "M-dup"]
        assert len(gen_rows) == 1
    finally:
        db.close()
