from __future__ import annotations

from app import config
from app.database import SessionLocal, init_db
from app.models import ModelOutput, OutputFlag


def setup_module(_m):
    init_db()


def test_config_defaults():
    assert config.POOL_EXCLUDED_COMPLETENESS_CATEGORIES == {"isolated-organ", "fragment"}
    # Curator-only flagging (internal instance) hides at the first flag.
    assert config.FLAG_HIDE_THRESHOLD == 1


def test_hidden_at_and_outputflag_exist():
    with SessionLocal() as db:
        cols = {c.name for c in ModelOutput.__table__.columns}
        assert "hidden_at" in cols
        fcols = {c.name for c in OutputFlag.__table__.columns}
        assert fcols == {"id", "output_id", "session_id", "reason", "created"}
