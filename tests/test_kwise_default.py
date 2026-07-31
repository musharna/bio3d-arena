"""K-wise is the default ballot, degrading to pairwise.

K-wise voting shipped complete — builder, endpoint, grid, mobile layout, reveal — and then sat
unreachable, because the ONLY way to ask for it was a hand-typed `?set=kwise`. Nothing in the UI
emitted that. A voter could not find it, so in practice every ballot was a pair.

That matters because a pair yields ONE Bradley-Terry relation per ballot and a 4-up ballot yields
THREE (the pick beats each of the three it was shown against). With the human vote pool the
scarce input on every board, serving pairs when a quad is available throws away two thirds of the
ranking information each voter offers.

`_build_kwise_comparison` already degrades to a pairwise comparison when no task can fill a quad,
so "serve the largest ballot this task supports" is a property the builder ALREADY has. These
tests pin the routing decision: the default asks for it, and `?set=pair` is the explicit opt-out.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import config, integrity
from app.database import SessionLocal
from app.main import app
from app.models import Category, Criterion, Generator, GoldPair, ModelOutput, Task
from tests.factories import cascade_delete, delete_outputs_matching

_PFX = "kwdef"


def _clear(db):
    delete_outputs_matching(db, ModelOutput.asset_path.like(f"{_PFX}/%"))
    cascade_delete(db, Task, Task.title.like("KWDEF %"))
    cascade_delete(db, Generator, Generator.slug.like(f"{_PFX}-g%"))
    cascade_delete(db, Category, Category.slug.like(f"{_PFX}-%"))
    db.commit()


def _seed(db, slug_suffix: str, title: str, n_generators: int):
    """One category holding one task with `n_generators` distinct same-paradigm outputs.

    Scoping each case to its own category lets a test ask for a task that CAN fill a quad and one
    that cannot, without depending on what the rest of the suite left in the shared database.
    The paradigm must be on the vote roster: `vote_pool_excluded_generator_ids` drops NULL-paradigm
    generators outright, so a fixture without one is silently invisible to the pool.
    """
    if db.execute(select(Criterion).where(Criterion.slug == "overall")).first() is None:
        db.add(Criterion(slug="overall", name="Overall"))
        db.commit()
    cat = Category(slug=f"{_PFX}-{slug_suffix}", name=f"KWDEF {slug_suffix}")
    db.add(cat)
    db.flush()
    task = Task(category_id=cat.id, title=f"KWDEF {title}", prompt="p")
    db.add(task)
    db.flush()
    for i in range(n_generators):
        g = Generator(
            slug=f"{_PFX}-g{slug_suffix}{i}",
            name=f"G{slug_suffix}{i}",
            paradigm="image_recon",
        )
        db.add(g)
        db.flush()
        db.add(
            ModelOutput(
                task_id=task.id,
                generator_id=g.id,
                asset_path=f"{_PFX}/{slug_suffix}{i}.glb",
                asset_format="glb",
                source="api:fal:trellis",
            )
        )
    db.commit()
    return cat, task


@pytest.fixture
def arena(monkeypatch):
    """A client plus two scoped categories: `quad` can fill a 4-up ballot, `pair` cannot.

    Admissibility is bypassed for the same reason the kingdom suite bypasses it — these fixture
    outputs have no mesh bytes on disk, and the rubric is orthogonal to which ballot shape gets
    routed.
    """
    monkeypatch.setattr("app.admissibility.non_admitted_output_ids", lambda db, rubric=None: set())
    with SessionLocal() as db:
        _clear(db)
        quad_cat, quad_task = _seed(db, "quad", "Quad Rose", 4)
        pair_cat, pair_task = _seed(db, "pair", "Pair Bolete", 3)
        yield TestClient(app), quad_cat.slug, pair_cat.slug, quad_task.id, pair_task.id
        _clear(db)


def test_default_next_serves_a_kwise_ballot_when_a_quad_exists(arena):
    """No `?set=` at all — the shape a real voter's browser requests — must be the 4-up ballot."""
    client, quad_slug, _pair_slug, quad_task_id, _ = arena
    r = client.get(f"/api/next?criterion=overall&category={quad_slug}")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("kind") == "kwise", f"default ballot was not k-wise: {data.keys()}"
    assert data["task"]["id"] == quad_task_id
    assert len(data["outputs"]) == 4
    # Four DISTINCT models: a repeat would manufacture a self-match in the BT fit.
    assert len({o["output_id"] for o in data["outputs"]}) == 4


def test_default_next_degrades_to_pairwise_when_no_quad_exists(arena):
    """The positive control for the test above: with only 3 generators the SAME default request
    must still serve a usable ballot rather than 404. A degrade that broke would otherwise read
    as 'k-wise is on' when the arena had actually gone dark."""
    client, _quad_slug, pair_slug, _, _ = arena
    r = client.get(f"/api/next?criterion=overall&category={pair_slug}")
    assert r.status_code == 200, r.text
    data = r.json()
    assert "kind" not in data, f"expected a pairwise fallback, got {data.get('kind')}"
    # The pairwise payload identifies its task by title; only the k-wise one carries an `id`.
    assert data["task"]["title"] == "KWDEF Pair Bolete"
    assert "comparison_id" in data


def test_set_pair_forces_pairwise_even_where_a_quad_is_available(arena):
    """The explicit opt-out. Without it there is no way to ask for 1v1 once the default flips."""
    client, quad_slug, _pair_slug, _, _ = arena
    r = client.get(f"/api/next?criterion=overall&category={quad_slug}&set=pair")
    assert r.status_code == 200, r.text
    data = r.json()
    assert "kind" not in data, "?set=pair still served a k-wise ballot"
    assert data["task"]["title"] == "KWDEF Quad Rose"
    assert "comparison_id" in data


def test_set_kwise_still_works(arena):
    """The old opt-in URL is now redundant but must not break — it is in the wild and in docs."""
    client, quad_slug, _pair_slug, _, _ = arena
    r = client.get(f"/api/next?criterion=overall&category={quad_slug}&set=kwise")
    assert r.status_code == 200, r.text
    assert r.json().get("kind") == "kwise"


def test_voting_a_pairwise_ballot_returns_a_kwise_follow_up(arena):
    """The pin-to-pairwise bug.

    `/api/vote` built its follow-up with the pairwise builder unconditionally. Once the default
    is k-wise, a voter who lands on a pair — via `?set=pair`, or via the degrade on a task that
    cannot fill a quad — would be handed a pair again, and again, for the rest of the session:
    one vote through the narrow door and the wide one is never offered.
    """
    client, quad_slug, _pair_slug, _, _ = arena
    first = client.get(f"/api/next?criterion=overall&category={quad_slug}&set=pair").json()
    assert "comparison_id" in first, first

    r = client.post(
        f"/api/vote?criterion=overall&category={quad_slug}",
        json={"comparison_id": first["comparison_id"], "winner": "a"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"  # positive control: the vote itself still records
    assert body["next"] is not None, "voter dead-ended after one vote"
    assert body["next"].get("kind") == "kwise", "follow-up ballot was pinned to pairwise"


def test_kwise_ballot_carries_the_reference_photos(arena, monkeypatch):
    """A k-wise ballot must show the reference photos, exactly as the pairwise one does.

    The k-wise payload carried `task: {id, title, prompt}` and no references, and the client
    hard-hid the reference panel to match. That was survivable while k-wise was an opt-in
    experiment. As the DEFAULT it would have quietly removed the reference photo from every
    ballot — and judging "which of these is a better Rosa" with nothing to compare against
    turns a fidelity benchmark into a beauty contest. More votes, worse votes.
    """
    client, quad_slug, _pair_slug, _, _ = arena
    refs = [{"url": "/media/ref/rose.jpg", "credit": "CC BY 2.0 — someone"}]
    monkeypatch.setattr("app.service.reference_images_for_task", lambda db, task: refs)

    data = client.get(f"/api/next?criterion=overall&category={quad_slug}").json()
    assert data.get("kind") == "kwise", data
    assert data["task"]["references"] == refs
    # The category chip is shared with the 2-up view; without this the k-wise ballot had
    # nothing to put in it and showed the literal string "K-wise" to voters instead.
    assert data["task"]["category"] == "KWDEF quad"

    # Positive control: the pairwise shape still carries them too, from the same source.
    pair = client.get(f"/api/next?criterion=overall&category={quad_slug}&set=pair").json()
    assert pair["task"]["references"] == refs


def test_gold_attention_checks_survive_the_kwise_default(arena, monkeypatch):
    """Attention checks must not go dark when the default ballot stops being pairwise.

    Gold injection lived INSIDE `_build_comparison`. That was invisible while the pairwise builder
    was the default entry point, and became a silent hole the moment it stopped being: the k-wise
    builder only reaches `_build_comparison` as a *fallback*, and on the live corpus 15 of 20
    active tasks can fill a quad — so the fallback would almost never fire and the whole
    trust/attention-check layer would quietly stop sampling voters.

    GOLD_RATE=1.0 makes this deterministic: every ballot must be the gold check, even though a
    quad is sitting right there for the taking.
    """
    client, quad_slug, _pair_slug, _, _ = arena
    monkeypatch.setattr(config, "GOLD_RATE", 1.0)
    integrity.reset_rate_limits()

    with SessionLocal() as db:
        task = db.execute(select(Task).where(Task.title == "KWDEF Quad Rose")).scalars().one()
        # generator_id is NOT NULL even for gold; `is_gold` is what keeps these out of
        # matchmaking and the rankings, so borrowing a fixture generator is harmless.
        gen_id = (
            db.execute(select(Generator.id).where(Generator.slug == f"{_PFX}-gquad0"))
            .scalars()
            .one()
        )
        good = ModelOutput(
            task_id=task.id,
            generator_id=gen_id,
            asset_path=f"{_PFX}/gold_good.glb",
            asset_format="glb",
            source="gold",
            is_gold=True,
        )
        bad = ModelOutput(
            task_id=task.id,
            generator_id=gen_id,
            asset_path=f"{_PFX}/gold_bad.glb",
            asset_format="glb",
            source="gold",
            is_gold=True,
        )
        db.add_all([good, bad])
        db.flush()
        db.add(GoldPair(task_id=task.id, good_output_id=good.id, bad_output_id=bad.id))
        db.commit()

    r = client.get(f"/api/next?criterion=overall&category={quad_slug}")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("kind") != "kwise", "a k-wise ballot displaced the attention check"
    assert "comparison_id" in data, data
