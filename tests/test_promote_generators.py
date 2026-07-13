# tests/test_promote_generators.py
"""promote_generators moves a named model's outputs + their evidence between DBs — and NOTHING
vote-derived.

The hazard this guards: the established promote pattern was a whole-DB file copy, which would
have dragged 124 local UI-test votes into the canonical study DB. This script copies only the
generator, its outputs, and output-describing evidence rows; every vote-derived table is
forbidden, and a table that is in neither list fails the run loudly rather than being silently
dropped or silently copied.
"""

import sqlite3

import pytest
from sqlalchemy import create_engine

from app.models import Base
from scripts.promote_generators import PromoteError, promote


def _fresh_db(path):
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    engine.dispose()
    return str(path)


def _seed_common(con):
    """category + task exist in BOTH dbs (a promote never invents tasks)."""
    con.execute("INSERT INTO category (id, slug, name, description) VALUES (1,'c','C','')")
    con.execute(
        "INSERT INTO task (id, category_id, title, prompt, criteria_note, active, created) "
        "VALUES (1,1,'t','p','',1,'2026-01-01')"
    )


def _seed_source(con):
    _seed_common(con)
    con.execute(
        "INSERT INTO generator (id, slug, name, description, kind, is_anonymous, paradigm) "
        "VALUES (85,'fal:new','New','','model',0,'image_recon')"
    )
    # n_comparisons is deliberately NON-zero: it accrued from local test votes in the source DB.
    con.execute(
        "INSERT INTO model_output (id, task_id, generator_id, title, asset_path, asset_format, "
        "meta_json, n_comparisons, is_gold, created, source) "
        "VALUES (581,1,85,'o','a.glb','glb','{}',7,0,'2026-01-01','api:fal:new')"
    )
    con.execute(
        "INSERT INTO admissibility (output_id, predicate, admit, reason, detail_json, version, "
        "computed) VALUES (581,'semantic',1,'','{}','semantic-v2','2026-01-01')"
    )
    con.execute(
        "INSERT INTO metric (output_id, chamfer, scorer_version, gt_version_hash, status, detail, "
        "computed) VALUES (581, 0.5, 'v1', 'h', 'ok', '', '2026-01-01')"
    )
    # vote-derived rows that must NEVER cross
    con.execute("INSERT INTO criterion (id, slug, name, description) VALUES (1,'overall','O','')")
    con.execute(
        "INSERT INTO comparison (id, task_id, output_a_id, output_b_id, criterion_id, session_id, "
        "is_gold, created) VALUES (1,1,581,581,1,'s',0,'2026-01-01')"
    )
    con.commit()


def test_promotes_generator_outputs_and_evidence(tmp_path):
    src, dst = _fresh_db(tmp_path / "src.db"), _fresh_db(tmp_path / "dst.db")
    with sqlite3.connect(src) as c:
        _seed_source(c)
    with sqlite3.connect(dst) as c:
        _seed_common(c)
        c.commit()

    summary = promote(src, dst, ["fal:new"], apply=True)

    with sqlite3.connect(dst) as c:
        assert c.execute("SELECT COUNT(*) FROM generator WHERE slug='fal:new'").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM model_output WHERE id=581").fetchone()[0] == 1
        assert (
            c.execute("SELECT COUNT(*) FROM admissibility WHERE output_id=581").fetchone()[0] == 1
        )
        assert c.execute("SELECT COUNT(*) FROM metric WHERE output_id=581").fetchone()[0] == 1
    assert summary["generators"] == 1
    assert summary["model_output"] == 1


def test_vote_derived_rows_never_cross(tmp_path):
    src, dst = _fresh_db(tmp_path / "src.db"), _fresh_db(tmp_path / "dst.db")
    with sqlite3.connect(src) as c:
        _seed_source(c)
    with sqlite3.connect(dst) as c:
        _seed_common(c)
        c.commit()

    promote(src, dst, ["fal:new"], apply=True)

    with sqlite3.connect(dst) as c:
        assert c.execute("SELECT COUNT(*) FROM comparison").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM vote").fetchone()[0] == 0


def test_n_comparisons_is_reset_to_zero(tmp_path):
    """The source's vote counter is meaningless in the target — its comparisons don't come with
    it. Carrying 7 over would show votes the target has no record of."""
    src, dst = _fresh_db(tmp_path / "src.db"), _fresh_db(tmp_path / "dst.db")
    with sqlite3.connect(src) as c:
        _seed_source(c)
    with sqlite3.connect(dst) as c:
        _seed_common(c)
        c.commit()

    promote(src, dst, ["fal:new"], apply=True)

    with sqlite3.connect(dst) as c:
        assert c.execute("SELECT n_comparisons FROM model_output WHERE id=581").fetchone()[0] == 0


def test_dry_run_writes_nothing(tmp_path):
    src, dst = _fresh_db(tmp_path / "src.db"), _fresh_db(tmp_path / "dst.db")
    with sqlite3.connect(src) as c:
        _seed_source(c)
    with sqlite3.connect(dst) as c:
        _seed_common(c)
        c.commit()

    summary = promote(src, dst, ["fal:new"], apply=False)

    assert summary["model_output"] == 1  # reports what it WOULD do
    with sqlite3.connect(dst) as c:
        assert c.execute("SELECT COUNT(*) FROM model_output").fetchone()[0] == 0


def test_id_collision_fails_loud(tmp_path):
    src, dst = _fresh_db(tmp_path / "src.db"), _fresh_db(tmp_path / "dst.db")
    with sqlite3.connect(src) as c:
        _seed_source(c)
    with sqlite3.connect(dst) as c:
        _seed_common(c)
        # target already holds output id 581 belonging to something else
        c.execute(
            "INSERT INTO generator (id, slug, name, description, kind, is_anonymous, paradigm) "
            "VALUES (99,'other','Other','','model',0,'image_recon')"
        )
        c.execute(
            "INSERT INTO model_output (id, task_id, generator_id, title, asset_path, asset_format,"
            " meta_json, n_comparisons, is_gold, created, source) "
            "VALUES (581,1,99,'x','x.glb','glb','{}',0,0,'2026-01-01','other')"
        )
        c.commit()

    with pytest.raises(PromoteError, match="collision"):
        promote(src, dst, ["fal:new"], apply=True)


def test_missing_task_in_target_fails_loud(tmp_path):
    """An output whose task doesn't exist in the target would be an orphan FK."""
    src, dst = _fresh_db(tmp_path / "src.db"), _fresh_db(tmp_path / "dst.db")
    with sqlite3.connect(src) as c:
        _seed_source(c)
    with sqlite3.connect(dst) as c:
        pass  # target has NO category/task

    with pytest.raises(PromoteError, match="task"):
        promote(src, dst, ["fal:new"], apply=True)


def test_unknown_generator_fails_loud(tmp_path):
    src, dst = _fresh_db(tmp_path / "src.db"), _fresh_db(tmp_path / "dst.db")
    with sqlite3.connect(src) as c:
        _seed_source(c)
    with sqlite3.connect(dst) as c:
        _seed_common(c)
        c.commit()

    with pytest.raises(PromoteError, match="not found"):
        promote(src, dst, ["fal:typo"], apply=True)


def test_rerun_is_idempotent(tmp_path):
    src, dst = _fresh_db(tmp_path / "src.db"), _fresh_db(tmp_path / "dst.db")
    with sqlite3.connect(src) as c:
        _seed_source(c)
    with sqlite3.connect(dst) as c:
        _seed_common(c)
        c.commit()

    promote(src, dst, ["fal:new"], apply=True)
    summary = promote(src, dst, ["fal:new"], apply=True)  # already there

    assert summary["model_output"] == 0
    with sqlite3.connect(dst) as c:
        assert c.execute("SELECT COUNT(*) FROM model_output").fetchone()[0] == 1
