# tests/test_commission_dispatch.py
"""A model is judged only on a script it actually returned.

The incident: mid-sweep the OpenRouter account ran out of credit. Every subsequent call returned
402 Payment Required — and run_batch's `except Exception` recorded each one as status="error"
AGAINST THE MODEL. qwen3.6-plus ended up with a 0/17 record for reasons that had nothing to do with
qwen. /procedural computes pass@1 from those rows, and commission_attempt is
UNIQUE(model_id, task_id), so the bogus failures were also PERMANENT: the pair could never be
retried. 30 rows had to be deleted by hand.

Two rules now:
  - An ACCOUNT failure (401/402/403) aborts the whole run. It is not a result, it is a stop.
  - A TRANSIENT failure (timeout, 429, 5xx) records NOTHING and moves on, so the pair stays
    retryable. Writing a row would burn the pair forever under the UNIQUE constraint.
"""

import pytest

from app.commission import HarnessError, run_batch
from app.database import SessionLocal, init_db
from app.models import Category, CommissionAttempt, Task, TraitRubric


def setup_module(_m):
    init_db()


class _HTTPError(Exception):
    """Stands in for httpx.HTTPStatusError: carries a response with a status_code."""

    def __init__(self, status_code):
        super().__init__(f"Client error '{status_code}' for url '...'")
        self.response = type("R", (), {"status_code": status_code})()


def _seed(db):
    cat = db.query(Category).filter_by(slug="dispatch-test").one_or_none()
    if cat is None:
        cat = Category(slug="dispatch-test", name="Dispatch test")
        db.add(cat)
        db.flush()
    task = Task(category_id=cat.id, title="t", prompt="p")
    db.add(task)
    db.flush()
    db.add(TraitRubric(task_id=task.id, taxon="Amanita muscaria"))
    db.flush()
    return [("Amanita muscaria", task.id)]


def _attempts(db, model_id):
    return db.query(CommissionAttempt).filter_by(model_id=model_id).all()


def test_payment_required_aborts_the_run_and_records_nothing(tmp_path):
    """402 is our account failing, not the model failing. Recording it would publish a billing
    problem as a model's pass@1 score — and burn the pair permanently."""
    with SessionLocal() as db:
        tt = _seed(db)

        def broke(model_id, prompt):
            raise _HTTPError(402)

        with pytest.raises(HarnessError, match="402"):
            run_batch(
                db,
                complete_fn=broke,
                run_fn=lambda s, o: {"status": "ok"},
                roster=["m-402"],
                taxon_tasks=tt,
                asset_dir=tmp_path,
            )

        assert _attempts(db, "m-402") == [], "a billing failure was recorded against the model"
        db.rollback()


@pytest.mark.parametrize("code", [401, 403])
def test_other_account_failures_also_abort(tmp_path, code):
    with SessionLocal() as db:
        tt = _seed(db)

        def broke(model_id, prompt):
            raise _HTTPError(code)

        with pytest.raises(HarnessError):
            run_batch(
                db,
                complete_fn=broke,
                run_fn=lambda s, o: {"status": "ok"},
                roster=[f"m-{code}"],
                taxon_tasks=tt,
                asset_dir=tmp_path,
            )
        db.rollback()


def test_transient_failure_records_nothing_so_the_pair_stays_retryable(tmp_path):
    """commission_attempt is UNIQUE(model_id, task_id): a row written for a timeout would block
    that model x task pair from EVER being retried. A blip must not cost a permanent zero."""
    with SessionLocal() as db:
        tt = _seed(db)

        def flaky(model_id, prompt):
            raise TimeoutError("read timeout")

        counts = run_batch(
            db,
            complete_fn=flaky,
            run_fn=lambda s, o: {"status": "ok"},
            roster=["m-flaky"],
            taxon_tasks=tt,
            asset_dir=tmp_path,
        )

        assert _attempts(db, "m-flaky") == [], "a transient blip was recorded as a model failure"
        assert counts["dispatch_failed"] == 1
        db.rollback()
