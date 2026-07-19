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
    # the agentic paradigm's render->critique->revise trail: how the output was MADE, not voted on
    con.execute(
        "INSERT INTO critique (output_id, render_path, critic_note, status, computed) "
        "VALUES (581,'renders/581.png','fins detached','ok','2026-01-01')"
    )
    # vote-derived rows that must NEVER cross
    con.execute("INSERT INTO criterion (id, slug, name, description) VALUES (1,'overall','O','')")
    con.execute(
        "INSERT INTO comparison (id, task_id, output_a_id, output_b_id, criterion_id, session_id, "
        "is_gold, created) VALUES (1,1,581,581,1,'s',0,'2026-01-01')"
    )
    con.commit()


def test_failed_attempts_promote_too_or_pass_at_1_is_inflated(tmp_path):
    """commission_attempt is what /procedural computes pass@1 from, and a FAILED attempt has
    output_id IS NULL. Copying it keyed by output would carry only the successes and silently
    report pass@1 as 100%. The attempt log travels with the GENERATOR, not with the output."""
    src, dst = _fresh_db(tmp_path / "src.db"), _fresh_db(tmp_path / "dst.db")
    with sqlite3.connect(src) as c:
        _seed_source(c)
        # a failed attempt: the model produced no output at all, so output_id IS NULL.
        # (commission_attempt is UNIQUE(model_id, task_id) — one attempt per model x task — so the
        # failure and the success below belong to different models of the same generator family.)
        c.execute(
            "INSERT INTO commission_attempt (id, task_id, model_id, generator_id, output_id, "
            "status, error, script, mesh_stats_json, duration_ms, created) "
            "VALUES (900,1,'m-failed',85,NULL,'invalid_mesh','','s','{}',10,'2026-01-01')"
        )
        # a successful one, linked to the promoted output
        c.execute(
            "INSERT INTO commission_attempt (id, task_id, model_id, generator_id, output_id, "
            "status, error, script, mesh_stats_json, duration_ms, created) "
            "VALUES (901,1,'m-ok',85,581,'ok','','s','{}',10,'2026-01-01')"
        )
        c.commit()
    with sqlite3.connect(dst) as c:
        _seed_common(c)
        c.commit()

    promote(src, dst, ["fal:new"], apply=True)

    with sqlite3.connect(dst) as c:
        statuses = {
            r[0] for r in c.execute("SELECT status FROM commission_attempt WHERE generator_id=85")
        }
    assert statuses == {"ok", "invalid_mesh"}, "the failed attempt was dropped — pass@1 inflated"


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
        # the agentic render->critique->revise trail travels with its output
        assert c.execute("SELECT COUNT(*) FROM critique WHERE output_id=581").fetchone()[0] == 1
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


def _seed_blank_paradigm_source(con, *, slug, source):
    """A generator born with a BLANK paradigm (the ingest default) + one output carrying `source`.
    This is the exact state a just-generated api:text/api: generator is in before backfill runs."""
    _seed_common(con)
    con.execute(
        "INSERT INTO generator (id, slug, name, description, kind, is_anonymous, paradigm) "
        f"VALUES (85,'{slug}','New','','model',0,'')"
    )
    con.execute(
        "INSERT INTO model_output (id, task_id, generator_id, title, asset_path, asset_format, "
        "meta_json, n_comparisons, is_gold, created, source) "
        f"VALUES (581,1,85,'o','a.glb','glb','{{}}',0,0,'2026-01-01','{source}')"
    )
    con.commit()


def test_blank_paradigm_generator_is_classified_on_promote(tmp_path):
    """A generator promoted with a blank paradigm (backfill not yet run) would land INVISIBLE on
    its board (boards filter paradigm==X). Promote auto-heals it from the output source via the
    canonical classifier — api:text: → text_native, even though the 'meshy' slug alone says
    image_recon. This removes the must-remember-to-backfill-before-promote dependency."""
    src, dst = _fresh_db(tmp_path / "src.db"), _fresh_db(tmp_path / "dst.db")
    with sqlite3.connect(src) as c:
        _seed_blank_paradigm_source(
            c, slug="fal:meshy-v6-text", source="api:text:fal:meshy-v6-text"
        )
    with sqlite3.connect(dst) as c:
        _seed_common(c)
        c.commit()

    promote(src, dst, ["fal:meshy-v6-text"], apply=True)

    with sqlite3.connect(dst) as c:
        p = c.execute("SELECT paradigm FROM generator WHERE slug='fal:meshy-v6-text'").fetchone()[0]
    assert p == "text_native"  # source prefix wins over the 'meshy' slug keyword


def test_unclassifiable_blank_paradigm_fails_loud(tmp_path):
    """A blank-paradigm generator the classifier can't map must NOT land on a board unclassified —
    promote fails loud so the omission is fixed deliberately, never silently."""
    src, dst = _fresh_db(tmp_path / "src.db"), _fresh_db(tmp_path / "dst.db")
    with sqlite3.connect(src) as c:
        _seed_blank_paradigm_source(c, slug="mystery-gen", source="weird:unmapped")
    with sqlite3.connect(dst) as c:
        _seed_common(c)
        c.commit()

    with pytest.raises(PromoteError, match="paradigm"):
        promote(src, dst, ["mystery-gen"], apply=True)


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
