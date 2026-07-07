"""Kingdom-scoped arena pool + matchmaking + /api/meta (Task 5): the arena section of the
kingdom test suite (kingdoms.py unit tests live in test_kingdoms.py; middleware/cookie tests
live in test_kingdom_scope.py)."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import matchmaking
from app.database import SessionLocal, init_db
from app.main import _build_comparison, _build_kwise_comparison, app
from app.models import Category, Criterion, Generator, ModelOutput, Task


def setup_module(_m):
    init_db()


def _mk(db):
    p = Category(slug="plants", name="Plants")
    f = Category(slug="fungi", name="Fungi")
    db.add_all([p, f])
    db.flush()
    tp = Task(category_id=p.id, title="Rose", prompt="", active=True)
    tf = Task(category_id=f.id, title="Bolete", prompt="", active=True)
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
