from sqlalchemy import select

from app import dataset
from app.models import Comparison, Vote, Criterion
from tests.test_public_export import _mk  # reuse SP1 seed helper


def test_build_preference_records_shape(db_session):
    e = _mk(db_session)
    # "overall" is a real seeded criterion slug (app.seed) committed outside this test's
    # rollback-isolated transaction by other test modules' setup_module(seed_all(...)) --
    # get-or-create to avoid a UNIQUE-constraint collision on full-suite runs (same pattern
    # as tests/test_recon_service.py, test_mode_a_scan_exclusion.py, test_difficulty_page.py).
    crit = db_session.execute(select(Criterion).where(Criterion.slug == "overall")).scalars().first()
    if crit is None:
        crit = Criterion(slug="overall", name="Overall")
        db_session.add(crit)
        db_session.flush()
    comp = Comparison(
        task_id=e["t_pub"].id,
        output_a_id=e["o_ok"].id,
        output_b_id=e["o_self"].id,
        criterion_id=crit.id,
        session_id="s1",
    )
    db_session.add(comp)
    db_session.flush()
    db_session.add(Vote(comparison_id=comp.id, winner="a", session_id="s1"))
    db_session.flush()
    rec = dataset.build_preference_records(db_session)
    # build_preference_records is unfiltered (matches the pre-refactor /api/export.json route:
    # every decided comparison ever cast, revealed post-hoc) -- other test modules' setup_module
    # (seed_all + real votes) commit rows outside this test's rollback-isolated transaction, so on
    # full-suite runs n_votes may exceed 1. Assert on our own comparison rather than the total
    # count (same >=-not-== convention as tests/test_research.py's export assertion, for the
    # identical cross-module-pollution reason).
    assert rec["n_votes"] >= 1
    v = next(x for x in rec["votes"] if x["comparison_id"] == comp.id)
    assert v["winner"] == "a" and v["generator_a"] == "lpy" and v["task"] == "maize-a"


def test_license_rollup_dedupes_and_nullsafe():
    rows = [
        {"license": "CC-BY-4.0", "attribution": "A", "source": "external"},
        {"license": "CC-BY-4.0", "attribution": "A", "source": "external"},
        {"license": None, "attribution": None, "source": "bio3d-arena"},
    ]
    roll = dataset.license_rollup(rows)
    assert {"license": "CC-BY-4.0", "attribution": "A", "source": "external"} in roll
    assert {"license": "", "attribution": "", "source": "bio3d-arena"} in roll
    assert len(roll) == 2


def test_render_license_and_datasheet_include_key_facts():
    roll = [{"license": "CC-BY-4.0", "attribution": "A", "source": "external"}]
    manifest = {"sha256": "abc123", "counts": {"model_output": 5, "task": 2}, "n_outputs": 5}
    lic = dataset.render_license(roll)
    ds = dataset.render_datasheet("2026.07-v1", manifest, roll)
    assert "CC-BY-4.0" in lic and "A" in lic
    assert "2026.07-v1" in ds and "abc123" in ds
    assert "held-out" in ds.lower() and "npy" not in ds.lower()  # GT-private note, no raw GT
