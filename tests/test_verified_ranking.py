from sqlalchemy import select

from app import service
from app.models import (
    Category,
    Criterion,
    Generator,
    Task,
    ModelOutput,
    Comparison,
    Vote,
    VoterSession,
    User,
)


def _seed_two_generators_one_vote(db, *, verified):
    # Look up the "overall" Criterion instead of unconditionally inserting one: when this
    # file runs after test_api.py / test_leaderboard.py in the same pytest session, those
    # modules' setup_module() has already committed a real "overall" Criterion to the
    # shared engine (outside this test's rollback-able transaction), so a blind insert
    # here would violate the slug UNIQUE constraint. Mirrors the existing
    # test_mode_a_scan_exclusion.py convention (query-then-create).
    cat = Category(slug="plant", name="Plant")
    crit = db.execute(select(Criterion).where(Criterion.slug == "overall")).scalars().first()
    if crit is None:
        crit = Criterion(slug="overall", name="Overall")
        db.add(crit)
    g1 = Generator(slug="g1", name="G1", kind="model")
    g2 = Generator(slug="g2", name="G2", kind="model")
    db.add_all([cat, g1, g2])
    db.flush()
    t = Task(category_id=cat.id, title="tk", prompt="p", active=True)
    db.add(t)
    db.flush()
    o1 = ModelOutput(task_id=t.id, generator_id=g1.id, asset_path="1.glb", source="bio3d-arena")
    o2 = ModelOutput(task_id=t.id, generator_id=g2.id, asset_path="2.glb", source="bio3d-arena")
    db.add_all([o1, o2])
    db.flush()
    comp = Comparison(
        task_id=t.id, output_a_id=o1.id, output_b_id=o2.id, criterion_id=crit.id, session_id="sv"
    )
    db.add(comp)
    db.flush()
    if verified:
        u = User(hf_id="hf-1", username="v")
        db.add(u)
        db.flush()
        db.add(VoterSession(session_id="sv", user_id=u.id))
    else:
        db.add(VoterSession(session_id="sv"))
    db.add(Vote(comparison_id=comp.id, winner="a", session_id="sv"))
    db.flush()
    return g1, g2


def test_verified_scope_ranks_verified_votes(db_session):
    _seed_two_generators_one_vote(db_session, verified=True)
    rows = service.verified_leaderboard_rows(db_session, "overall", "all")
    # verified vote produced a decisive game -> both generators ranked, each with n_games>0
    assert rows and all(r["n_games"] > 0 for r in rows)
    assert all(
        {"generator", "bt_score", "bt_lower", "bt_upper", "n_games", "rank"} <= set(r) for r in rows
    )


def test_anonymous_vote_absent_from_verified_scope(db_session):
    _seed_two_generators_one_vote(db_session, verified=False)
    rows = service.verified_leaderboard_rows(db_session, "overall", "all")
    assert rows == []  # no verified votes -> no verified games -> empty verified board
