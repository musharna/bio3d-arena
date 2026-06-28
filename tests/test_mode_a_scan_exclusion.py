"""P0-1: GT/reference scans must be excluded from the Mode-A perceptual ranking
(leaderboard / significance / BT), while remaining valid DB outputs elsewhere."""

from __future__ import annotations

import random

from app import service
from app.database import SessionLocal, init_db
from app.models import (
    Category,
    Comparison,
    Criterion,
    Generator,
    ModelOutput,
    Task,
    Vote,
)


def _seed(db):
    """Two AI generators + one reference-scan generator, with votes where the scan wins."""
    cat = Category(slug="c-modea-%d" % random.randint(0, 10**6), name="Plants")
    db.add(cat)
    db.flush()
    task = Task(category_id=cat.id, title="t-modea", prompt="p", active=True)
    db.add(task)
    db.flush()
    crit = db.execute(select_overall()).scalars().first()
    if crit is None:
        crit = Criterion(slug="overall", name="Overall")
        db.add(crit)
        db.flush()

    def mk(slug, source):
        g = Generator(slug="g-modea-%s-%d" % (slug, random.randint(0, 10**6)), name="M-%s" % slug)
        db.add(g)
        db.flush()
        o = ModelOutput(
            task_id=task.id,
            generator_id=g.id,
            asset_path="seed/x.glb",
            asset_format="glb",
            source=source,
        )
        db.add(o)
        db.flush()
        return g, o

    g_ai1, o_ai1 = mk("ai1", "api:fal:trellis")
    g_ai2, o_ai2 = mk("ai2", "recon:trellis-mv")
    g_scan, o_scan = mk("scan", "rose-x")

    def add_vote(oa, ob, winner):
        sid = "s-%d" % random.randint(0, 10**9)
        comp = Comparison(
            task_id=task.id,
            criterion_id=crit.id,
            output_a_id=oa.id,
            output_b_id=ob.id,
            is_gold=False,
            session_id=sid,
        )
        db.add(comp)
        db.flush()
        db.add(Vote(comparison_id=comp.id, winner=winner, session_id=sid))

    # 5 votes: scan beats ai1 every time (would dominate BT if counted).
    for _ in range(5):
        add_vote(o_scan, o_ai1, "a")
    # one ai1 vs ai2 vote so the AI generators have a real match between them
    add_vote(o_ai1, o_ai2, "a")
    db.commit()
    return crit, cat, g_ai1, g_ai2, g_scan


def select_overall():
    from sqlalchemy import select

    return select(Criterion).where(Criterion.slug == "overall")


def test_reference_scan_generator_ids_detects_scan_gen():
    init_db()
    db = SessionLocal()
    try:
        _crit, _cat, _ai1, _ai2, g_scan = _seed(db)
        ref = service.reference_scan_generator_ids(db)
        assert g_scan.id in ref
    finally:
        db.close()


def test_matches_exclude_scan_generator():
    init_db()
    db = SessionLocal()
    try:
        crit, cat, g_ai1, g_ai2, g_scan = _seed(db)
        matches = service._matches_for_scope(db, crit.id, cat.id)
        flat = {p for m in matches for p in m}
        assert g_scan.id not in flat  # scan never appears as winner or loser
        assert g_ai1.id in flat and g_ai2.id in flat  # the ai-vs-ai match survives
    finally:
        db.close()


def test_players_exclude_scan_generator():
    init_db()
    db = SessionLocal()
    try:
        _crit, cat, _ai1, _ai2, g_scan = _seed(db)
        players = service._players_for_scope(db, cat.id)
        assert g_scan.id not in players
    finally:
        db.close()


def test_generator_display_names_disambiguates_shared_names():
    init_db()
    db = SessionLocal()
    try:
        r = random.randint(0, 10**6)
        # two generators share a display name; one is unique
        a = Generator(slug=f"xfrog-AG15-s2-{r}", name="XfrogPlants (botanical)")
        b = Generator(slug=f"xfrog-AG20-s5-{r}", name="XfrogPlants (botanical)")
        u = Generator(slug=f"trellis-{r}", name="TRELLIS (fal)")
        db.add_all([a, b, u])
        db.flush()
        names = service.generator_display_names(db)
        assert names[u.id] == "TRELLIS (fal)"  # unique name unchanged
        assert names[a.id] != names[b.id]  # shared name disambiguated
        assert names[a.id].startswith("XfrogPlants (botanical) · ")
        assert "AG15-s2" in names[a.id] and "AG20-s5" in names[b.id]
    finally:
        db.close()


def test_leaderboard_rows_omit_scan_generator():
    from app.main import _leaderboard_rows

    init_db()
    db = SessionLocal()
    try:
        crit, cat, _ai1, _ai2, g_scan = _seed(db)
        service.recompute_scope(db, crit, cat.id)
        rows = _leaderboard_rows(db, "overall", cat.slug)
        names = {r["generator"] for r in rows}
        assert "M-scan" not in names
    finally:
        db.close()
