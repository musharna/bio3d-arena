"""Synthetic-plant ingest + the end-to-end scoped vote→leaderboard chain.

The chain is: seed the synthetic-plants board → ingest two entrants onto one species task →
`/api/next` serves a pair scoped to that category+criterion → `/api/vote` → `/admin/recompute`
→ `/api/leaderboard` shows the two with games. Every link between ingest and a rank lives in
this chain and nowhere else in the suite.

WHY THERE ARE TWO TESTS HERE

The chain was only ever exercised with the AgriGen recon bake-off GLBs, at an absolute path
in a sibling tree. Those are absent on every CI runner, so the one test guarding the chain
skipped on every commit and ran only on a workstation that happened to have them -- the same
"the real-execution test is exactly the one CI skips" shape that
tests/test_ingest_paradigm.py was split out to fix. A regression invisible to the thing that
runs on every commit is not guarded.

So the chain now runs twice:

* `test_scoped_vote_chain_with_built_assets` builds its GLBs with trimesh, needs nothing
  outside the repo, and runs everywhere. It is also the STRICTER of the two: it controls
  every entrant, so it can assert that exactly they appear on the board and that their
  ratings come out in the order it voted -- which is what "the vote reached the leaderboard"
  actually means.
* `test_synth_ingest_then_scoped_vote_ranks` keeps the real bake-off assets where they exist.
  Built boxes say nothing about real generator output: multi-megabyte meshes, real materials
  and textures, the actual byte layout a recon model emits. That is the on-disk boundary, and
  a synthetic mesh cannot stand in for it.

The two use DIFFERENT species tasks on purpose. The suite shares one SQLite DB across the
run, so pairing both on one task would let `/api/next` serve a mixed pair and put five
generators on one board -- the built-asset test's "exactly these" assertion would then fail
for a reason that has nothing to do with the chain.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import trimesh
from fastapi.testclient import TestClient

from sqlalchemy import select

from app import config, ingest, seed
from app.database import SessionLocal, init_db
from app.main import app
from app.models import Category, Comparison, ModelOutput, Task

# The recon bake-off GLBs double as "generated plants" for the cross-paradigm matchup. They
# live in the sibling AgriGen tree, absent in CI / on other checkouts — skip there.
BAKE = Path("/home/mjarnold/agrigen/backend/data/bakeoff_v1")

SCOPE = "category=synthetic-plants&criterion=botanical_plausibility"


def setup_module(_module):
    init_db()


def _votable_paradigm() -> str:
    """A paradigm the arena actually serves, read from config rather than hardcoded.

    `/api/next` filters the pool by config.ARENA_VOTE_PARADIGMS, so an entrant off that roster
    is ingested, displayed, and never served for voting -- and the vote loop below would spin
    20 times and cast nothing. Hardcoding the value would keep passing if the roster were
    retuned to exclude it.
    """
    assert config.ARENA_VOTE_PARADIGMS, "no vote roster configured; these tests prove nothing"
    return sorted(config.ARENA_VOTE_PARADIGMS)[0]


def _ingest_pair(db, task, entrants, paradigm):
    """Put both entrants on `task`. `entrants` is [(generator_slug, glb_bytes), ...]."""
    for slug, data in entrants:
        ingest.register_output(
            db,
            task_id=task.id,
            generator_slug=slug,
            data=data,
            ext="glb",
            title=f"{task.title} — {slug}",
            meta={"synthetic": True},
            paradigm=paradigm,
        )
    db.commit()
    assert db.query(ModelOutput).filter(ModelOutput.task_id == task.id).count() == len(entrants)


def _cast_votes(client, db, task, prefer, want, scope: str = SCOPE) -> int:
    """Drive `/api/next` → `/api/vote` on the scoped board until `want` votes land.

    `prefer` is the slugs best-first; the earlier of the two served always wins, so the
    outcome is a fixed round-robin standing rather than something order-dependent. A voted
    comparison is never served again, so at most one vote exists per pair -- `want` must be
    n*(n-1)/2 over the entrants, and asking for more is what proves the pool served them all.
    """
    out_gen = {
        o.id: o.generator.slug
        for o in db.query(ModelOutput).filter(ModelOutput.task_id == task.id).all()
    }
    cast = 0
    for _ in range(20 * want):
        nxt = client.get(f"/api/next?{scope}").json()
        cid = nxt.get("comparison_id")
        if cid is None:
            break  # pool exhausted: every pair on this task has been voted
        comp = db.get(Comparison, cid)
        if comp.is_gold:
            continue
        a, b = out_gen.get(comp.output_a_id), out_gen.get(comp.output_b_id)
        if a is None or b is None:
            continue  # a pair from another task on this shared board
        winner = "a" if prefer.index(a) < prefer.index(b) else "b"
        if (
            client.post(
                f"/api/vote?{scope}",
                json={"comparison_id": cid, "winner": winner},
            ).status_code
            == 200
        ):
            cast += 1
        if cast >= want:
            break
    return cast


RIVAL = "rival-board"
RIVAL_SCOPE = f"category={RIVAL}&criterion=botanical_plausibility"


def _seed_rival_category_votes(db, client) -> None:
    """Put two entrants in a DIFFERENT category and vote them on the same criterion.

    Nothing else in the suite votes on botanical_plausibility, so without this the
    whole-corpus rating set and the synthetic-plants rating set are identical and the
    caller's category filter is unobservable.
    """
    cat = db.execute(select(Category).where(Category.slug == RIVAL)).scalars().first()
    if cat is None:
        cat = Category(slug=RIVAL, name="Rival Board")
        db.add(cat)
        db.flush()
    task = Task(category_id=cat.id, title="rival — botanical plausibility", prompt="p")
    db.add(task)
    db.commit()

    par = _votable_paradigm()
    _ingest_pair(
        db,
        task,
        [
            ("rival-a", trimesh.creation.capsule().export(file_type="glb")),
            (
                "rival-b",
                trimesh.creation.annulus(r_min=0.5, r_max=1.0, height=1.0).export(file_type="glb"),
            ),
        ],
        par,
    )
    cast = _cast_votes(client, db, task, ["rival-a", "rival-b"], want=1, scope=RIVAL_SCOPE)
    assert cast == 1, (
        "the rival category cast no vote, so it cannot act as the scoping control -- this "
        "test would then pass on a leaderboard that ignores ?category= entirely"
    )


def _recompute_and_read_board(client, scope: str = SCOPE) -> list[dict]:
    assert client.post("/admin/recompute", data={"token": "test-token"}).status_code == 200
    board = client.get(f"/api/leaderboard?{scope}").json()
    return [r for r in board["rows"] if r.get("n_games", 0) > 0]


def test_scoped_vote_chain_with_built_assets():
    """The whole chain, on assets built here -- so it runs on every commit.

    Every entrant is ours, so this asserts what the real-asset test cannot: that the board
    holds exactly them, and that their ratings come out in the order the votes were cast.
    "At least one generator has games" is satisfied by a board that ignored the votes
    entirely, and by rows another test left behind on the shared DB.

    THREE entrants, not two. Two would give one pair and one game -- and the middle entrant
    is the control that makes the ordering mean something: it goes 1-1, so it must land
    BETWEEN the other two. A pipeline that recorded appearances rather than which side won
    would leave all three tied, and a two-entrant version could not tell that apart from a
    working one.
    """
    db = SessionLocal()
    try:
        seed.seed_synthetic_plants(db)
        db.commit()
        # pinus_sylvestris, not the zea_mays the real-asset test uses -- see module docstring.
        task = seed.synth_task_for_slug(db, "pinus_sylvestris")
        assert task is not None

        # Three DIFFERENT meshes: identical bytes for two entrants would dedup to one asset,
        # and this would stop saying anything about distinct entrants.
        best, mid, worst = "built-best", "built-mid", "built-worst"
        _ingest_pair(
            db,
            task,
            [
                (best, trimesh.creation.box().export(file_type="glb")),
                (mid, trimesh.creation.icosphere().export(file_type="glb")),
                (worst, trimesh.creation.cylinder(radius=1.0, height=2.0).export(file_type="glb")),
            ],
            _votable_paradigm(),
        )

        client = TestClient(app)
        prefer = [best, mid, worst]
        # 3 entrants -> 3 pairs -> 3 votes, giving 2-0 / 1-1 / 0-2.
        cast = _cast_votes(client, db, task, prefer, want=3)
        assert cast == 3, f"only {cast} of 3 pairs were served and voted on the scoped board"

        # A rival category voting on the SAME criterion. This is the negative control for the
        # "scoped" half of the chain: without it, no other category has votes on
        # botanical_plausibility, so the whole-corpus rating and the synthetic-plants rating
        # hold the same rows -- and dropping the `?category=` filter entirely from the
        # leaderboard query changes nothing observable. Found by mutation: that exact edit
        # (`Rating.category_id == category_id` -> `.is_(None)`) left this test green.
        _seed_rival_category_votes(db, client)

        rows = _recompute_and_read_board(client)
        by_slug = {r["generator"]: r for r in rows}
        assert set(by_slug) == set(prefer), (
            f"the scoped board must hold exactly this category's entrants; got {sorted(by_slug)}"
        )
        for slug in prefer:
            assert by_slug[slug]["n_games"] == 2, (
                f"{slug} played both its pairs but the board reports "
                f"{by_slug[slug]['n_games']} games"
            )

        scores = [by_slug[s]["bt_score"] for s in prefer]
        assert scores[0] > scores[1] > scores[2], (
            f"votes went {prefer[0]} > {prefer[1]} > {prefer[2]}, but the board rates them "
            f"{dict(zip(prefer, scores))} -- the votes did not reach the rating"
        )
    finally:
        db.close()


@pytest.mark.skipif(not BAKE.exists(), reason="AgriGen bakeoff_v1 GLBs not present")
def test_synth_ingest_then_scoped_vote_ranks():
    """The same chain on real recon output -- the on-disk boundary a built box cannot cover."""
    db = SessionLocal()
    try:
        seed.seed_synthetic_plants(db)
        db.commit()
        task = seed.synth_task_for_slug(db, "zea_mays")
        assert task is not None
        # Use the API-served slugs: the bare "trellis"/"hunyuan3d" slugs are the self-hosted
        # recon dups, now in config.APP_HIDDEN_GENERATOR_SLUGS (internal-only), so they'd be
        # excluded from the arena pool and no votable pair would form. The "fal:" variants carry
        # the same recon identity but stay displayable.
        _ingest_pair(
            db,
            task,
            [
                ("fal:trellis", (BAKE / "zea_mays__trellis.glb").read_bytes()),
                ("fal:hunyuan3d", (BAKE / "zea_mays__hunyuan3d.glb").read_bytes()),
            ],
            # RECON_GENERATORS is defined as single-image->3D reconstructors, and these fal:
            # entries carry that same identity. Without this the generators are created with a
            # NULL paradigm, which is off config.ARENA_VOTE_PARADIGMS, so /api/next never
            # returns a votable pair and no vote is cast.
            "image_recon",
        )

        client = TestClient(app)
        cast = _cast_votes(client, db, task, ["fal:trellis", "fal:hunyuan3d"], want=1)
        assert cast >= 1

        rows = _recompute_and_read_board(client)
        assert rows, "expected at least one generator with games in the scoped board"
    finally:
        db.close()
