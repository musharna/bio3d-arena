"""Kingdom-scoped arena pool + matchmaking + /api/meta (Task 5): the arena section of the
kingdom test suite (kingdoms.py unit tests live in test_kingdoms.py; middleware/cookie tests
live in test_kingdom_scope.py). Leaderboard + significance on-the-fly scoping (Task 6) lives in
the "leaderboard scoping" section at the bottom of this file."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import matchmaking, service
from app.database import SessionLocal, init_db
from app.main import _build_comparison, _build_kwise_comparison, _leaderboard_rows, app
from app.models import (
    Category,
    Comparison,
    Criterion,
    Generator,
    ModelOutput,
    Task,
    TaskDifficulty,
    Vote,
)


def setup_module(_m):
    init_db()


def _mk(db):
    # Unique slugs (not the real "plants"/"fungi"): other modules commit those via
    # seed_all(force=True) on the shared DB, so raw slugs collide in the full suite.
    # These tests reference categories by id, so the slug value is immaterial.
    p = Category(slug="mkt-plants", name="MKT Plants")
    f = Category(slug="mkt-fungi", name="MKT Fungi")
    db.add_all([p, f])
    db.flush()
    tp = Task(category_id=p.id, title="MKT Rose", prompt="", active=True)
    tf = Task(category_id=f.id, title="MKT Bolete", prompt="", active=True)
    db.add_all([tp, tf])
    db.flush()
    return p, f, tp, tf


def test_pick_task_respects_category_id_set(db_session):
    db = db_session
    p, f, tp, tf = _mk(db)
    picks = {matchmaking.pick_task(db, category_ids={f.id}) for _ in range(8)}
    picks.discard(None)
    assert picks == {tf} or picks <= {tf}  # only fungi task eligible


def test_pick_task_empty_category_ids_set_yields_none(db_session):
    """An empty set (kingdom with zero mapped categories) must yield no eligible task,
    not fall back to 'all tasks'."""
    db = db_session
    _mk(db)
    assert matchmaking.pick_task(db, category_ids=set()) is None


def test_pick_task_category_id_still_works(db_session):
    """Existing single-category_id callers (category selector, unrelated to kingdom) regress."""
    db = db_session
    p, f, tp, tf = _mk(db)
    picks = {matchmaking.pick_task(db, category_id=p.id) for _ in range(8)}
    picks.discard(None)
    assert picks <= {tp}


# --------------------------------------------------------------------- builder-level threading

_PFX = "kfx"


def _clear_builder_fixtures(db):
    db.query(ModelOutput).filter(ModelOutput.asset_path.like(f"{_PFX}/%")).delete(
        synchronize_session=False
    )
    db.query(Task).filter(Task.title.like("KFX %")).delete(synchronize_session=False)
    db.query(Generator).filter(Generator.slug.like(f"{_PFX}-g%")).delete(synchronize_session=False)
    db.query(Category).filter(Category.slug.like(f"{_PFX}-%")).delete(synchronize_session=False)
    db.commit()


def _seed_builder_fixtures(db):
    """A plants task + a fungi task, each with 4 same-paradigm outputs (a full votable quad,
    so both pairwise pick_pair AND kwise pick_quad have a real group to select from)."""
    _clear_builder_fixtures(db)
    if db.execute(select(Criterion).where(Criterion.slug == "overall")).first() is None:
        db.add(Criterion(slug="overall", name="Overall"))
        db.commit()
    p = Category(slug=f"{_PFX}-plants", name="KFX Plants")
    f = Category(slug=f"{_PFX}-fungi", name="KFX Fungi")
    db.add_all([p, f])
    db.flush()
    tp = Task(category_id=p.id, title="KFX Rose", prompt="p")
    tf = Task(category_id=f.id, title="KFX Bolete", prompt="p")
    db.add_all([tp, tf])
    db.flush()
    for task, tag in ((tp, "p"), (tf, "f")):
        for i in range(4):
            g = Generator(slug=f"{_PFX}-g{tag}{i}", name=f"G{tag}{i}")
            db.add(g)
            db.flush()
            db.add(
                ModelOutput(
                    task_id=task.id,
                    generator_id=g.id,
                    asset_path=f"{_PFX}/{tag}{i}.glb",
                    asset_format="glb",
                    source="api:fal:trellis",
                )
            )
    db.commit()
    return p, f, tp, tf


def test_build_comparison_scopes_to_kingdom(monkeypatch):
    """_build_comparison, given a kingdom mapped to only the fungi category, never serves
    the plants task."""
    import app.kingdoms as kingdoms_mod

    with SessionLocal() as db:
        p, f, tp, tf = _seed_builder_fixtures(db)
        # Admissibility gating is orthogonal to kingdom scoping (Task 5's concern); the bare
        # fixture outputs above have no real mesh bytes on disk, so bypass that predicate here
        # rather than fabricate real GLBs just to exercise an unrelated code path.
        monkeypatch.setattr(
            "app.admissibility.non_admitted_output_ids", lambda db, rubric=None: set()
        )
        monkeypatch.setitem(kingdoms_mod.CATEGORY_SLUGS_IN, "fungi", frozenset({f.slug}))
        monkeypatch.setitem(kingdoms_mod.KINGDOM_OF, f.slug, "fungi")

        seen_titles = set()
        for _ in range(20):
            payload = _build_comparison(db, "sess-kfx-1", None, None, kingdom="fungi")
            if payload is not None:
                seen_titles.add(payload["task"]["title"])
        assert seen_titles <= {tf.title}
        assert tp.title not in seen_titles


def test_build_kwise_comparison_scopes_to_kingdom(monkeypatch):
    """_build_kwise_comparison restricts its own task query by kingdom too (it may fall back
    to pairwise when no task has a quad, but must still never surface the plants task)."""
    import app.kingdoms as kingdoms_mod

    with SessionLocal() as db:
        p, f, tp, tf = _seed_builder_fixtures(db)
        monkeypatch.setattr(
            "app.admissibility.non_admitted_output_ids", lambda db, rubric=None: set()
        )
        monkeypatch.setitem(kingdoms_mod.CATEGORY_SLUGS_IN, "fungi", frozenset({f.slug}))
        monkeypatch.setitem(kingdoms_mod.KINGDOM_OF, f.slug, "fungi")

        seen_titles = set()
        for _ in range(20):
            payload = _build_kwise_comparison(db, "sess-kfx-2", None, None, kingdom="fungi")
            if payload is not None:
                seen_titles.add(payload["task"]["title"])
        assert seen_titles <= {tf.title}
        assert tp.title not in seen_titles


def test_api_meta_scopes_categories_to_active_kingdom(monkeypatch):
    with SessionLocal() as db:
        _clear_builder_fixtures(db)
        p = Category(slug=f"{_PFX}-plants", name="KFX Plants")
        f = Category(slug=f"{_PFX}-fungi", name="KFX Fungi")
        db.add_all([p, f])
        db.commit()

    import app.kingdoms as kingdoms_mod

    monkeypatch.setitem(kingdoms_mod.CATEGORY_SLUGS_IN, "fungi", frozenset({f"{_PFX}-fungi"}))
    monkeypatch.setitem(kingdoms_mod.KINGDOM_OF, f"{_PFX}-fungi", "fungi")

    c = TestClient(app)
    c.cookies.set("bio3d_kingdom", "fungi")
    r = c.get("/api/meta")
    assert r.status_code == 200
    slugs = {cat["slug"] for cat in r.json()["categories"]}
    assert f"{_PFX}-plants" not in slugs
    assert f"{_PFX}-fungi" in slugs

    with SessionLocal() as db:
        _clear_builder_fixtures(db)


# --------------------------------------------------------------------- leaderboard scoping (Task 6)
#
# Cached `Rating` rows are keyed by a single category_id and cannot represent a kingdom (a SET
# of categories), so the leaderboard computes kingdom-scoped rows on the fly instead (mirroring
# verified_leaderboard_rows's on-demand-BT shape) — see service.kingdom_leaderboard_rows and
# main._leaderboard_rows.

_LBPFX = "klb"


def _clear_lb_fixtures(db):
    db.query(Vote).filter(Vote.session_id.like(f"{_LBPFX}-%")).delete(synchronize_session=False)
    db.query(Comparison).filter(Comparison.session_id.like(f"{_LBPFX}-%")).delete(
        synchronize_session=False
    )
    db.query(ModelOutput).filter(ModelOutput.asset_path.like(f"{_LBPFX}/%")).delete(
        synchronize_session=False
    )
    db.query(Task).filter(Task.title.like("KLB %")).delete(synchronize_session=False)
    db.query(Generator).filter(Generator.slug.like(f"{_LBPFX}-g%")).delete(
        synchronize_session=False
    )
    db.query(Category).filter(Category.slug.like(f"{_LBPFX}-%")).delete(synchronize_session=False)
    db.commit()


def _seed_lb_fixtures(db):
    """A KLB-plants category (2 generators, 1 decisive vote) + a KLB-fungi category (2 DIFFERENT
    generators, 1 decisive vote) — disjoint generator sets per category so a kingdom-scoped
    leaderboard that excludes cross-kingdom players is actually exercised, not a no-op over an
    empty overlap."""
    _clear_lb_fixtures(db)
    crit = db.execute(select(Criterion).where(Criterion.slug == "overall")).scalars().first()
    if crit is None:
        crit = Criterion(slug="overall", name="Overall")
        db.add(crit)
        db.flush()
    p = Category(slug=f"{_LBPFX}-plants", name="KLB Plants")
    f = Category(slug=f"{_LBPFX}-fungi", name="KLB Fungi")
    db.add_all([p, f])
    db.flush()
    tp = Task(category_id=p.id, title="KLB Rose", prompt="p", active=True)
    tf = Task(category_id=f.id, title="KLB Bolete", prompt="p", active=True)
    db.add_all([tp, tf])
    db.flush()

    def _mk_pair(task, tag):
        g1 = Generator(slug=f"{_LBPFX}-g{tag}1", name=f"KLB-G{tag}1")
        g2 = Generator(slug=f"{_LBPFX}-g{tag}2", name=f"KLB-G{tag}2")
        db.add_all([g1, g2])
        db.flush()
        o1 = ModelOutput(task_id=task.id, generator_id=g1.id, asset_path=f"{_LBPFX}/{tag}1.glb")
        o2 = ModelOutput(task_id=task.id, generator_id=g2.id, asset_path=f"{_LBPFX}/{tag}2.glb")
        db.add_all([o1, o2])
        db.flush()
        comp = Comparison(
            task_id=task.id,
            output_a_id=o1.id,
            output_b_id=o2.id,
            criterion_id=crit.id,
            session_id=f"{_LBPFX}-s-{tag}",
        )
        db.add(comp)
        db.flush()
        db.add(Vote(comparison_id=comp.id, winner="a", session_id=f"{_LBPFX}-s-{tag}"))
        db.commit()
        return g1, g2

    gp1, gp2 = _mk_pair(tp, "p")
    gf1, gf2 = _mk_pair(tf, "f")
    return p, f, gp1, gp2, gf1, gf2


def _patch_kingdom_to_fungi(monkeypatch, fungi_slug):
    import app.kingdoms as kingdoms_mod

    monkeypatch.setitem(kingdoms_mod.CATEGORY_SLUGS_IN, "fungi", frozenset({fungi_slug}))
    monkeypatch.setitem(kingdoms_mod.KINGDOM_OF, fungi_slug, "fungi")


def test_leaderboard_rows_kingdom_scoping_excludes_other_kingdom_players(monkeypatch):
    """The function the /leaderboard route calls: with kingdom='fungi' active, only the fungi
    generators (which have decisive matches in that kingdom) appear — the plants-only pair,
    despite having its own decisive vote, must not appear."""
    with SessionLocal() as db:
        p, f, gp1, gp2, gf1, gf2 = _seed_lb_fixtures(db)
        fungi_slug = f.slug
        _patch_kingdom_to_fungi(monkeypatch, fungi_slug)

        rows = _leaderboard_rows(db, "overall", None, None, "fungi")
        names = {r["generator"] for r in rows}
        assert names == {gf1.name, gf2.name}
        assert gp1.name not in names
        assert gp2.name not in names
        assert all(r["n_games"] > 0 for r in rows)

        _clear_lb_fixtures(db)


def test_leaderboard_rows_all_kingdom_keeps_cached_path_unaffected(monkeypatch):
    """kingdom='all' (the default) must still take the cached-Rating path rather than the new
    live-BT kingdom path — i.e. our fresh votes (never fed through recompute_scope, so they have
    no cached Rating row yet) must NOT appear, unlike the live-computed kingdom='fungi' path
    exercised above. This pins the branch selection itself: a regression that routed 'all'
    through the live path too would make this fail by suddenly finding our fixture's generators."""
    with SessionLocal() as db:
        p, f, gp1, gp2, gf1, gf2 = _seed_lb_fixtures(db)
        # No Rating rows have been computed for these ad-hoc fixtures (recompute_scope was
        # never called) -> if 'all' took the live path it WOULD find them (same votes the
        # kingdom='fungi' test above found live); the cached path must not.
        rows = _leaderboard_rows(db, "overall", None, None, "all")
        names = {r["generator"] for r in rows}
        assert gp1.name not in names
        assert gp2.name not in names
        assert gf1.name not in names
        assert gf2.name not in names
        _clear_lb_fixtures(db)


def test_leaderboard_route_kingdom_query_param_scopes_rows(monkeypatch):
    """End-to-end: GET /leaderboard?kingdom=fungi renders only the fungi generators' names, and
    hides the (global-only) VLM judge board section entirely while a single kingdom is active."""
    with SessionLocal() as db:
        p, f, gp1, gp2, gf1, gf2 = _seed_lb_fixtures(db)
        fungi_slug = f.slug

    _patch_kingdom_to_fungi(monkeypatch, fungi_slug)

    c = TestClient(app)
    r = c.get("/leaderboard?kingdom=fungi")
    assert r.status_code == 200
    body = r.text
    assert gf1.name in body
    assert gf2.name in body
    assert gp1.name not in body
    assert gp2.name not in body
    assert "VLM judge" not in body  # judge board deferred/hidden under an active kingdom

    with SessionLocal() as db:
        _clear_lb_fixtures(db)


def test_compute_significance_accepts_category_ids_set(monkeypatch):
    """compute_significance's new category_ids kwarg scopes to a kingdom the same way the
    leaderboard does (used by main.significance_page when a kingdom is active)."""
    with SessionLocal() as db:
        p, f, gp1, gp2, gf1, gf2 = _seed_lb_fixtures(db)
        sig = service.compute_significance(db, "overall", category_ids={f.id})
        assert sig["status"] == "ok"
        assert set(sig["labels"]) == {gf1.name, gf2.name}
        _clear_lb_fixtures(db)


# --------------------------------------------------------------------- Task 7: remaining pages
#
# tasks / coverage / dataset-export / difficulty / benchmark / spotlight. Follows the same
# monkeypatch-CATEGORY_SLUGS_IN/KINGDOM_OF pattern as the builder/leaderboard sections above.

_T7PFX = "kt7"


def _clear_t7_fixtures(db):
    db.query(ModelOutput).filter(ModelOutput.asset_path.like(f"{_T7PFX}/%")).delete(
        synchronize_session=False
    )
    db.query(Comparison).filter(Comparison.session_id.like(f"{_T7PFX}-%")).delete(
        synchronize_session=False
    )
    db.query(Vote).filter(Vote.session_id.like(f"{_T7PFX}-%")).delete(synchronize_session=False)
    db.query(Task).filter(Task.title.like("KT7 %")).delete(synchronize_session=False)
    db.query(Generator).filter(Generator.slug.like(f"{_T7PFX}-g%")).delete(
        synchronize_session=False
    )
    db.query(Category).filter(Category.slug.like(f"{_T7PFX}-%")).delete(synchronize_session=False)
    db.commit()


def _seed_t7_fixtures(db):
    """A plants task + a fungi task under fresh KT7-prefixed categories, each with a real
    output (+ one decisive vote) so coverage/dataset-export rows have something to show."""
    _clear_t7_fixtures(db)
    crit = db.execute(select(Criterion).where(Criterion.slug == "overall")).scalars().first()
    if crit is None:
        crit = Criterion(slug="overall", name="Overall")
        db.add(crit)
        db.flush()
    p = Category(slug=f"{_T7PFX}-plants", name="KT7 Plants")
    f = Category(slug=f"{_T7PFX}-fungi", name="KT7 Fungi")
    db.add_all([p, f])
    db.flush()
    tp = Task(category_id=p.id, title="KT7 Plant Task", prompt="p", active=True)
    tf = Task(category_id=f.id, title="KT7 Fungi Task", prompt="p", active=True)
    db.add_all([tp, tf])
    db.flush()

    def _mk_output_and_vote(task, tag):
        g1 = Generator(slug=f"{_T7PFX}-g{tag}1", name=f"KT7-G{tag}1")
        g2 = Generator(slug=f"{_T7PFX}-g{tag}2", name=f"KT7-G{tag}2")
        db.add_all([g1, g2])
        db.flush()
        o1 = ModelOutput(task_id=task.id, generator_id=g1.id, asset_path=f"{_T7PFX}/{tag}1.glb")
        o2 = ModelOutput(task_id=task.id, generator_id=g2.id, asset_path=f"{_T7PFX}/{tag}2.glb")
        db.add_all([o1, o2])
        db.flush()
        comp = Comparison(
            task_id=task.id,
            output_a_id=o1.id,
            output_b_id=o2.id,
            criterion_id=crit.id,
            session_id=f"{_T7PFX}-s-{tag}",
        )
        db.add(comp)
        db.flush()
        db.add(Vote(comparison_id=comp.id, winner="a", session_id=f"{_T7PFX}-s-{tag}"))
        db.commit()

    _mk_output_and_vote(tp, "p")
    _mk_output_and_vote(tf, "f")
    return p, f, tp, tf


def test_tasks_page_scopes_to_kingdom(monkeypatch):
    with SessionLocal() as db:
        p, f, tp, tf = _seed_t7_fixtures(db)
        _patch_kingdom_to_fungi(monkeypatch, f.slug)

        c = TestClient(app)
        r = c.get("/tasks?kingdom=fungi")
        assert r.status_code == 200
        assert tf.title in r.text
        assert tp.title not in r.text

        _clear_t7_fixtures(db)


def test_coverage_page_scopes_task_rows_to_kingdom(monkeypatch):
    with SessionLocal() as db:
        p, f, tp, tf = _seed_t7_fixtures(db)
        _patch_kingdom_to_fungi(monkeypatch, f.slug)

        c = TestClient(app)
        r = c.get("/coverage?kingdom=fungi")
        assert r.status_code == 200
        assert tf.title in r.text
        assert tp.title not in r.text

        _clear_t7_fixtures(db)


def test_difficulty_page_scopes_tier_species_to_kingdom(monkeypatch):
    with SessionLocal() as db:
        p, f, tp, tf = _seed_t7_fixtures(db)
        from app import difficulty as difficulty_mod

        difficulty_mod.set_task_difficulty(db, tp.id, "easy", commit=True)
        difficulty_mod.set_task_difficulty(db, tf.id, "easy", commit=True)
        _patch_kingdom_to_fungi(monkeypatch, f.slug)

        c = TestClient(app)
        r = c.get("/difficulty?kingdom=fungi")
        assert r.status_code == 200
        assert tf.title in r.text
        assert tp.title not in r.text

        db.query(TaskDifficulty).filter(TaskDifficulty.task_id.in_([tp.id, tf.id])).delete(
            synchronize_session=False
        )
        db.commit()
        _clear_t7_fixtures(db)


def test_benchmark_page_task_picker_scopes_to_kingdom(monkeypatch):
    with SessionLocal() as db:
        p, f, tp, tf = _seed_t7_fixtures(db)
        _patch_kingdom_to_fungi(monkeypatch, f.slug)

        c = TestClient(app)
        r = c.get("/benchmark?kingdom=fungi")
        assert r.status_code == 200
        assert tf.title in r.text
        assert tp.title not in r.text

        _clear_t7_fixtures(db)


def test_export_dataset_scopes_votes_to_kingdom(monkeypatch):
    with SessionLocal() as db:
        p, f, tp, tf = _seed_t7_fixtures(db)
        _patch_kingdom_to_fungi(monkeypatch, f.slug)

        c = TestClient(app)
        r = c.get("/api/export.json?kingdom=fungi")
        assert r.status_code == 200
        body = r.json()
        tasks_seen = {row["task"] for row in body["votes"]}
        assert tf.title in tasks_seen
        assert tp.title not in tasks_seen

        _clear_t7_fixtures(db)


def test_spotlight_index_filters_by_kingdom(monkeypatch):
    """spotlight_index filters SPOTLIGHTS by the active kingdom; every current subject is
    tagged 'plants', so kingdom=fungi must hide them all while kingdom=all keeps them."""
    c = TestClient(app)
    r_all = c.get("/spotlight?kingdom=all")
    assert r_all.status_code == 200
    assert "Solanum lycopersicum" in r_all.text  # tomato spotlight visible under 'all'

    r_fungi = c.get("/spotlight?kingdom=fungi")
    assert r_fungi.status_code == 200
    assert "Solanum lycopersicum" not in r_fungi.text
    assert "Zea mays" not in r_fungi.text
