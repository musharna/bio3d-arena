"""Kingdom leaderboard BT cache (fix 27s plants leaderboard): `KingdomRating` +
`KingdomJudgeRating` cache tables, populated by `service.recompute_all`/`recompute_judge_all`,
read-with-fallback by `main._leaderboard_rows` / `main._kingdom_judge_leaderboard_rows`.

Fixtures use a PREFIXED category slug (not raw "fungi") -- other test modules commit a real
"fungi" category via seed_all(force=True) on the shared suite DB, so a raw slug would collide.
The kingdom NAME reused is still "fungi" (kingdoms.KINGDOMS is a fixed 3-tuple), monkeypatched to
map onto this file's prefixed category only -- same convention as
test_kingdom_filtering.py's `_patch_kingdom_to_fungi`. Because the `KingdomRating`/
`KingdomJudgeRating` cache is keyed by the kingdom STRING (not by which categories currently map
to it), this file cleans its own cache rows up by generator_id (not by kingdom) at the end of
every test so nothing leaks into test_kingdom_filtering.py's own "fungi" fixtures sharing the
same suite run.
"""

from __future__ import annotations

from sqlalchemy import select

from app import service
from app.database import SessionLocal, init_db
from app.main import _kingdom_judge_leaderboard_rows, _leaderboard_rows
from app.models import (
    Category,
    Comparison,
    Criterion,
    Generator,
    JudgeRating,
    JudgeVote,
    KingdomJudgeRating,
    KingdomRating,
    ModelOutput,
    Rating,
    Task,
    Vote,
)
from tests.factories import cascade_delete


def setup_module(_m):
    init_db()


_PFX = "kc"


def _clear_fixtures(db):
    gen_ids = [
        g.id
        for g in db.execute(select(Generator).where(Generator.slug.like(f"{_PFX}-g%"))).scalars()
    ]
    if gen_ids:
        # Cache rows are keyed by kingdom STRING (shared with test_kingdom_filtering.py's own
        # "fungi" fixtures in the same suite run), so scope the delete to OUR generator ids
        # rather than kingdom="fungi" -- precise cleanup that can't touch another file's rows.
        # Also purge the GLOBAL Rating/JudgeRating rows recompute_all/recompute_judge_all leave
        # behind (category_id=None covers every category, ours included) -- SQLite can reissue
        # a deleted generator's rowid to our next _seed() call, which would otherwise make a
        # stale global Rating row from an earlier test look like a fresh, valid one.
        db.query(KingdomRating).filter(KingdomRating.generator_id.in_(gen_ids)).delete(
            synchronize_session=False
        )
        db.query(KingdomJudgeRating).filter(KingdomJudgeRating.generator_id.in_(gen_ids)).delete(
            synchronize_session=False
        )
        db.query(Rating).filter(Rating.generator_id.in_(gen_ids)).delete(synchronize_session=False)
        db.query(JudgeRating).filter(JudgeRating.generator_id.in_(gen_ids)).delete(
            synchronize_session=False
        )
    task_ids = [t.id for t in db.execute(select(Task).where(Task.title.like("KC %"))).scalars()]
    if task_ids:
        db.query(JudgeVote).filter(JudgeVote.task_id.in_(task_ids)).delete(
            synchronize_session=False
        )
    db.query(Vote).filter(Vote.session_id.like(f"{_PFX}-%")).delete(synchronize_session=False)
    cascade_delete(db, Comparison, Comparison.session_id.like(f"{_PFX}-%"))
    cascade_delete(db, ModelOutput, ModelOutput.asset_path.like(f"{_PFX}/%"))
    cascade_delete(db, Task, Task.title.like("KC %"))
    cascade_delete(db, Generator, Generator.slug.like(f"{_PFX}-g%"))
    cascade_delete(db, Category, Category.slug.like(f"{_PFX}-%"))
    db.commit()


def _seed(db):
    """A fungi-kingdom category with 2 generators, 1 decisive human vote (g1 beats g2) and 9
    decisive JudgeVotes (g1 beats g2) -- real signal for both the human and judge kingdom
    caches to compute + cache."""
    _clear_fixtures(db)
    crit = db.execute(select(Criterion).where(Criterion.slug == "overall")).scalars().first()
    if crit is None:
        crit = Criterion(slug="overall", name="Overall")
        db.add(crit)
        db.flush()
    cat = Category(slug=f"{_PFX}-fungi", name="KC Fungi")
    db.add(cat)
    db.flush()
    task = Task(category_id=cat.id, title="KC Bolete", prompt="p", active=True)
    db.add(task)
    db.flush()
    g1 = Generator(slug=f"{_PFX}-g1", name="KC-G1")
    g2 = Generator(slug=f"{_PFX}-g2", name="KC-G2")
    db.add_all([g1, g2])
    db.flush()
    o1 = ModelOutput(task_id=task.id, generator_id=g1.id, asset_path=f"{_PFX}/1.glb")
    o2 = ModelOutput(task_id=task.id, generator_id=g2.id, asset_path=f"{_PFX}/2.glb")
    db.add_all([o1, o2])
    db.flush()
    comp = Comparison(
        task_id=task.id,
        output_a_id=o1.id,
        output_b_id=o2.id,
        criterion_id=crit.id,
        session_id=f"{_PFX}-s1",
    )
    db.add(comp)
    db.flush()
    db.add(Vote(comparison_id=comp.id, winner="a", session_id=f"{_PFX}-s1"))
    for i in range(9):
        db.add(
            JudgeVote(
                task_id=task.id,
                output_a_id=o1.id,
                output_b_id=o2.id,
                criterion_id=crit.id,
                winner="a",
                view_condition="multi4",
                judge_model="claude-sonnet-4-6",
                swap_group=f"{_PFX}-jg-{i}",
                rationale="",
            )
        )
    db.commit()
    return cat, task, g1, g2


def _patch_kingdom(monkeypatch, fungi_slug):
    import app.kingdoms as kingdoms_mod

    monkeypatch.setitem(kingdoms_mod.CATEGORY_SLUGS_IN, "fungi", frozenset({fungi_slug}))
    monkeypatch.setitem(kingdoms_mod.KINGDOM_OF, fungi_slug, "fungi")


# --------------------------------------------------------------------- cache miss -> fallback


def test_cache_miss_falls_back_to_on_the_fly(monkeypatch):
    """Before any /admin/recompute, KingdomRating is empty for our kingdom -- the leaderboard
    route must still render correct rows via the on-the-fly fallback."""
    with SessionLocal() as db:
        cat, task, g1, g2 = _seed(db)
        _patch_kingdom(monkeypatch, cat.slug)

        assert service.cached_kingdom_leaderboard_rows(db, "overall", "fungi") is None

        rows = _leaderboard_rows(db, "overall", None, None, "fungi")
        names = {r["generator"] for r in rows}
        assert names == {g1.name, g2.name}
        assert all(r["n_games"] > 0 for r in rows)

        _clear_fixtures(db)


def test_judge_cache_miss_falls_back_to_on_the_fly(monkeypatch):
    with SessionLocal() as db:
        cat, task, g1, g2 = _seed(db)
        _patch_kingdom(monkeypatch, cat.slug)

        assert (
            service.cached_kingdom_judge_leaderboard_rows(db, "overall", "multi4", "fungi") is None
        )

        rows = _kingdom_judge_leaderboard_rows(db, "overall", "multi4", "fungi", {cat.id})
        names = {r["generator"] for r in rows}
        assert names == {g1.name, g2.name}
        assert all(r["n_games"] > 0 for r in rows)

        _clear_fixtures(db)


# --------------------------------------------------------------------- after recompute: cached == on-the-fly


def test_recompute_all_populates_cache_matching_on_the_fly(monkeypatch):
    with SessionLocal() as db:
        cat, task, g1, g2 = _seed(db)
        _patch_kingdom(monkeypatch, cat.slug)

        live = {
            r["generator"]: r for r in service.kingdom_leaderboard_rows(db, "overall", {cat.id})
        }

        service.recompute_all(db)

        cached_rows = (
            db.execute(select(KingdomRating).where(KingdomRating.generator_id.in_([g1.id, g2.id])))
            .scalars()
            .all()
        )
        assert len(cached_rows) == 2
        assert {r.kingdom for r in cached_rows} == {"fungi"}

        cached = service.cached_kingdom_leaderboard_rows(db, "overall", "fungi")
        assert cached is not None
        by_name = {r["generator"]: r for r in cached}
        assert set(by_name) == set(live)
        for name, live_row in live.items():
            assert by_name[name]["n_games"] == live_row["n_games"]
            assert abs(by_name[name]["bt_score"] - live_row["bt_score"]) < 0.15

        # The route helper (cache-first) must agree with the direct cache read above.
        route_rows = {
            r["generator"]: r for r in _leaderboard_rows(db, "overall", None, None, "fungi")
        }
        assert set(route_rows) == set(by_name)
        for name in by_name:
            assert route_rows[name]["n_games"] == by_name[name]["n_games"]

        _clear_fixtures(db)


def test_judge_recompute_all_populates_cache_matching_on_the_fly(monkeypatch):
    with SessionLocal() as db:
        cat, task, g1, g2 = _seed(db)
        _patch_kingdom(monkeypatch, cat.slug)

        live = {
            r["generator"]: r
            for r in service.kingdom_judge_leaderboard_rows(db, "overall", "multi4", {cat.id})
        }

        service.recompute_judge_all(db, view_condition="multi4")

        cached_rows = (
            db.execute(
                select(KingdomJudgeRating).where(
                    KingdomJudgeRating.generator_id.in_([g1.id, g2.id])
                )
            )
            .scalars()
            .all()
        )
        assert len(cached_rows) == 2
        assert {r.kingdom for r in cached_rows} == {"fungi"}
        assert {r.view_condition for r in cached_rows} == {"multi4"}

        cached = service.cached_kingdom_judge_leaderboard_rows(db, "overall", "multi4", "fungi")
        assert cached is not None
        by_name = {r["generator"]: r for r in cached}
        assert set(by_name) == set(live)
        for name, live_row in live.items():
            assert by_name[name]["n_games"] == live_row["n_games"]
            assert abs(by_name[name]["bt_score"] - live_row["bt_score"]) < 0.15

        route_rows = {
            r["generator"]: r
            for r in _kingdom_judge_leaderboard_rows(db, "overall", "multi4", "fungi", {cat.id})
        }
        assert set(route_rows) == set(by_name)

        _clear_fixtures(db)


# --------------------------------------------------------------------- proves cache is actually read


def test_leaderboard_route_reads_stale_cache_not_live(monkeypatch):
    """After recompute, a fresh vote that would move the on-the-fly score is added WITHOUT a
    second recompute -- the route must keep serving the cached (pre-new-vote) snapshot, proving
    it reads the cache rather than always recomputing live."""
    with SessionLocal() as db:
        cat, task, g1, g2 = _seed(db)
        _patch_kingdom(monkeypatch, cat.slug)
        service.recompute_all(db)

        cached_before = {
            r["generator"]: r
            for r in service.cached_kingdom_leaderboard_rows(db, "overall", "fungi")
        }

        crit = db.execute(select(Criterion).where(Criterion.slug == "overall")).scalars().first()
        o1 = (
            db.execute(select(ModelOutput).where(ModelOutput.generator_id == g1.id))
            .scalars()
            .first()
        )
        o2 = (
            db.execute(select(ModelOutput).where(ModelOutput.generator_id == g2.id))
            .scalars()
            .first()
        )
        comp2 = Comparison(
            task_id=task.id,
            output_a_id=o2.id,
            output_b_id=o1.id,
            criterion_id=crit.id,
            session_id=f"{_PFX}-s2",
        )
        db.add(comp2)
        db.flush()
        db.add(Vote(comparison_id=comp2.id, winner="a", session_id=f"{_PFX}-s2"))
        db.commit()

        # Sanity: the fresh vote DOES move the live/on-the-fly computation -- otherwise this
        # test would pass vacuously.
        live_after = {
            r["generator"]: r for r in service.kingdom_leaderboard_rows(db, "overall", {cat.id})
        }
        assert live_after[g2.name]["n_games"] != cached_before[g2.name]["n_games"]

        rows = _leaderboard_rows(db, "overall", None, None, "fungi")
        by_name = {r["generator"]: r for r in rows}
        assert by_name[g1.name]["n_games"] == cached_before[g1.name]["n_games"]
        assert by_name[g2.name]["n_games"] == cached_before[g2.name]["n_games"]

        _clear_fixtures(db)


# --------------------------------------------------------------------- all-path regression


def test_all_kingdom_path_unaffected_by_kingdom_cache(monkeypatch):
    """Regression: kingdom='all' must still take the cached global `Rating` path, never the new
    `KingdomRating` cache -- pins branch selection itself (independent of
    test_kingdom_filtering.py's own coverage of the same invariant)."""
    with SessionLocal() as db:
        cat, task, g1, g2 = _seed(db)
        _patch_kingdom(monkeypatch, cat.slug)
        # No recompute_all call -- our fixture has no cached (category_id=None) Rating row yet,
        # so if 'all' took the live kingdom path it WOULD find these fresh votes; it must not.
        rows = _leaderboard_rows(db, "overall", None, None, "all")
        names = {r["generator"] for r in rows}
        assert g1.name not in names
        assert g2.name not in names

        _clear_fixtures(db)
