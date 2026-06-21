"""Synthetic-plant ingest + end-to-end scoped vote→leaderboard. The recon bake-off GLBs are
"generated plants" too — reused here for the cross-paradigm (procedural vs reconstructed)
matchup, validating the votes-only scope reuse with real assets."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import ingest, seed
from app.database import SessionLocal, init_db
from app.main import app
from app.models import Comparison, ModelOutput

# The recon bake-off GLBs double as "generated plants" for the cross-paradigm matchup. They
# live in the sibling AgriGen tree, absent in CI / on other checkouts — skip there.
BAKE = Path("/home/mjarnold/agrigen/backend/data/bakeoff_v1")


def setup_module(_module):
    init_db()


@pytest.mark.skipif(not BAKE.exists(), reason="AgriGen bakeoff_v1 GLBs not present")
def test_synth_ingest_then_scoped_vote_ranks():
    db = SessionLocal()
    try:
        seed.seed_synthetic_plants(db)
        db.commit()
        task = seed.synth_task_for_slug(db, "zea_mays")
        assert task is not None
        for path, gen in [
            (BAKE / "zea_mays__trellis.glb", "trellis"),
            (BAKE / "zea_mays__hunyuan3d.glb", "hunyuan3d"),
        ]:
            ingest.register_output(
                db,
                task_id=task.id,
                generator_slug=gen,
                data=path.read_bytes(),
                ext="glb",
                title=f"zea_mays — {gen}",
                meta={"synthetic": True},
            )
        db.commit()
        assert db.query(ModelOutput).filter(ModelOutput.task_id == task.id).count() == 2

        client = TestClient(app)
        out_gen = {
            o.id: o.generator.slug
            for o in db.query(ModelOutput).filter(ModelOutput.task_id == task.id).all()
        }
        cast = 0
        for _ in range(20):
            nxt = client.get(
                "/api/next?category=synthetic-plants&criterion=botanical_plausibility"
            ).json()
            cid = nxt.get("comparison_id")
            if cid is None:
                continue
            comp = db.get(Comparison, cid)
            if comp.is_gold:
                continue
            winner = "a" if out_gen.get(comp.output_a_id) == "trellis" else "b"
            if (
                client.post(
                    "/api/vote?category=synthetic-plants&criterion=botanical_plausibility",
                    json={"comparison_id": cid, "winner": winner},
                ).status_code
                == 200
            ):
                cast += 1
            if cast >= 1:
                break
        assert cast >= 1
        assert client.post("/admin/recompute", data={"token": "test-token"}).status_code == 200
        board = client.get(
            "/api/leaderboard?category=synthetic-plants&criterion=botanical_plausibility"
        ).json()
        names = [r["generator"] for r in board["rows"] if r.get("n_games", 0) > 0]
        assert names, "expected at least one generator with games in the scoped board"
    finally:
        db.close()
