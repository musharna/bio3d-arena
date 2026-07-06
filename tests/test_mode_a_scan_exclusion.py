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
        matches, _groups = service._matches_for_scope(db, crit.id, cat.id)
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


def test_gen_name_honors_passed_names_dict():
    """Perf contract: _gen_name uses a passed names dict (no per-row full-table scan)."""
    from app.recon_service import _gen_name

    init_db()
    db = SessionLocal()
    try:
        # A gid with no Generator row resolves purely from the passed dict — proving the
        # loop-hoisted names map is honored and no fallback query is needed.
        assert _gen_name(db, 10**9, {10**9: "Provided Name"}) == "Provided Name"
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


# ── P1-2: untextured (geometry-only) outputs excluded from Mode-A ────────────────


def test_is_untextured_output_reads_meta_flag():
    from app.sourcing import is_untextured_output

    init_db()
    db = SessionLocal()
    try:
        r = random.randint(0, 10**6)
        cat = Category(slug=f"c-ut-{r}", name="Plants")
        db.add(cat)
        db.flush()
        task = Task(category_id=cat.id, title="t-ut", prompt="p")
        g = Generator(slug=f"g-ut-{r}", name="M-ut")
        db.add_all([task, g])
        db.flush()
        flagged = ModelOutput(
            task_id=task.id,
            generator_id=g.id,
            asset_path="seed/x.glb",
            asset_format="glb",
            source="bio3d-arena",
            meta_json='{"untextured": true}',
        )
        plain = ModelOutput(
            task_id=task.id,
            generator_id=g.id,
            asset_path="seed/x.glb",
            asset_format="glb",
            source="bio3d-arena",
            meta_json="{}",
        )
        db.add_all([flagged, plain])
        db.flush()
        assert is_untextured_output(flagged) is True
        assert is_untextured_output(plain) is False
    finally:
        db.close()


def test_untextured_generator_ids_requires_all_outputs_flagged():
    init_db()
    db = SessionLocal()
    try:
        r = random.randint(0, 10**6)
        cat = Category(slug=f"c-utg-{r}", name="Plants")
        db.add(cat)
        db.flush()
        task = Task(category_id=cat.id, title="t-utg", prompt="p")
        pure = Generator(slug=f"g-pure-{r}", name="M-pure")  # all outputs untextured
        mixed = Generator(slug=f"g-mixed-{r}", name="M-mixed")  # one textured → keep
        db.add_all([task, pure, mixed])
        db.flush()

        def out(g, meta):
            o = ModelOutput(
                task_id=task.id,
                generator_id=g.id,
                asset_path="seed/x.glb",
                asset_format="glb",
                source="bio3d-arena",
                meta_json=meta,
            )
            db.add(o)
            return o

        out(pure, '{"untextured": true}')
        out(pure, '{"untextured": true}')
        out(mixed, '{"untextured": true}')
        out(mixed, "{}")  # a real textured output
        db.commit()

        ut = service.untextured_generator_ids(db)
        assert pure.id in ut
        assert mixed.id not in ut  # has a textured output → not fully untextured
        assert pure.id in service.mode_a_excluded_generator_ids(db)
    finally:
        db.close()
