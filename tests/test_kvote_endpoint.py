import json
from app.database import SessionLocal, init_db
from app.models import KBallot, Comparison, ModelOutput


def setup_module(_m):
    """resolve_kballot feeds derived votes through the existing service.apply_vote, which
    does db.get(ModelOutput, ...) and requires the row to exist (not FK-enforced by sqlite,
    but dereferenced in Python) — so seed real ModelOutput rows at the literal ids the tests
    reference (10-13, 20-23) rather than leaving them dangling."""
    init_db()
    with SessionLocal() as db:
        for oid in (10, 11, 12, 13, 20, 21, 22, 23):
            if db.get(ModelOutput, oid) is None:
                db.add(
                    ModelOutput(
                        id=oid, task_id=1, generator_id=1, asset_path=f"seed/kvote-{oid}.glb"
                    )
                )
        db.commit()


def test_resolve_kballot_decomposes_to_three_pairs():
    from app import service

    with SessionLocal() as db:
        b = KBallot(
            task_id=1, criterion_id=1, session_id="s", output_ids_json=json.dumps([10, 11, 12, 13])
        )
        db.add(b)
        db.flush()
        n = service.resolve_kballot(db, b, best_output_id=10, session_id="s")
        assert n == 3
        assert b.resolved is True
        comps = db.query(Comparison).filter_by(ballot_id=b.id).all()
        assert len(comps) == 3
        # every derived comparison has best (10) in slot a and winner 'a'
        for c in comps:
            assert c.output_a_id == 10 and c.output_b_id in (11, 12, 13)
            assert c.vote.winner == "a"
        db.rollback()


def test_resolve_all_bad_makes_no_relations():
    from app import service

    with SessionLocal() as db:
        b = KBallot(
            task_id=1, criterion_id=1, session_id="s", output_ids_json=json.dumps([20, 21, 22, 23])
        )
        db.add(b)
        db.flush()
        n = service.resolve_kballot(db, b, best_output_id=None, session_id="s")
        assert n == 0
        assert b.resolved is True
        assert db.query(Comparison).filter_by(ballot_id=b.id).count() == 0
        db.rollback()
