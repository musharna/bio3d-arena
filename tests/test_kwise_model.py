import json
from app import seed
from app.database import SessionLocal, init_db
from app.models import KBallot, Comparison


def setup_module(_m):
    init_db()


def test_kballot_in_force_delete_models():
    assert KBallot in seed._FORCE_DELETE_MODELS


def test_kballot_and_ballot_id_persist():
    with SessionLocal() as db:
        b = KBallot(
            task_id=1, criterion_id=1, session_id="s", output_ids_json=json.dumps([1, 2, 3, 4])
        )
        db.add(b)
        db.flush()
        assert b.resolved is False
        c = Comparison(
            task_id=1, output_a_id=1, output_b_id=2, criterion_id=1, session_id="s", ballot_id=b.id
        )
        db.add(c)
        db.flush()
        assert c.ballot_id == b.id
        db.rollback()
