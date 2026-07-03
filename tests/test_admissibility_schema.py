# tests/test_admissibility_schema.py
from __future__ import annotations

from app import seed as seed_mod
from app.database import init_db
from app.models import Admissibility


def setup_module(_m):
    init_db()


def test_admissibility_columns():
    cols = {c.name for c in Admissibility.__table__.columns}
    assert cols == {
        "id",
        "output_id",
        "predicate",
        "admit",
        "reason",
        "detail_json",
        "version",
        "computed",
    }


def test_admissibility_in_force_delete_models():
    assert Admissibility in seed_mod._FORCE_DELETE_MODELS
