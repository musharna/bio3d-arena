"""TDD for the head-to-head record on the model-detail page (`/models/{slug}`, Task 8 / #74).

A rank should feel *earned*: the detail page must show who this model actually beat and how
often. The record is rendered straight from `service.head_to_head_record` — same-paradigm by
construction, ties counted with the 0.5 convention as exactly ONE game — so the page never
invents numbers the ranking math doesn't back.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import (
    Category,
    Comparison,
    Criterion,
    Generator,
    ModelOutput,
    Task,
    Vote,
)
from app.seed import seed_all

client = TestClient(app)

# The suite shares ONE temp DB — every fixture row below carries an `mdh2h-` slug prefix so it
# can never collide with another module's generators/categories (Generator.slug is UNIQUE).
PFX = "mdh2h"


def setup_module(_module):
    seed_all(force=True)


def _gen(db, slug: str, name: str, paradigm: str = "image_recon") -> Generator:
    g = Generator(slug=f"{PFX}-{slug}", name=name, kind="model", paradigm=paradigm)
    db.add(g)
    db.flush()
    return g


def _seed_h2h(db) -> tuple[Generator, Generator]:
    """Champ beats Challenger 3–1 (n=4, no ties) on one task."""
    crit = db.query(Criterion).filter_by(slug="overall").first()
    cat = Category(slug=f"{PFX}-cat", name="MdH2H Cat")
    db.add(cat)
    db.flush()
    task = Task(category_id=cat.id, title=f"{PFX}-task", prompt="p")
    db.add(task)
    db.flush()

    champ = _gen(db, "champ", "MdhChampModel")
    chall = _gen(db, "challenger", "MdhChallengerModel")
    oa = ModelOutput(
        task_id=task.id, generator_id=champ.id, asset_path="mdh-a.glb", asset_format="glb"
    )
    ob = ModelOutput(
        task_id=task.id, generator_id=chall.id, asset_path="mdh-b.glb", asset_format="glb"
    )
    db.add_all([oa, ob])
    db.flush()

    for winner in ("a", "a", "a", "b"):
        c = Comparison(
            task_id=task.id,
            criterion_id=crit.id,
            output_a_id=oa.id,
            output_b_id=ob.id,
            session_id=f"{PFX}-sess",
            is_gold=False,
        )
        db.add(c)
        db.flush()
        db.add(Vote(comparison_id=c.id, winner=winner, session_id=f"{PFX}-sess"))
    db.commit()
    return champ, chall


def test_model_detail_renders_head_to_head_section():
    """Every model page carries the section — its absence is what made a rank feel unearned."""
    r = client.get("/models/gen-alpha")
    assert r.status_code == 200
    assert "Head-to-head" in r.text
    # Same-paradigm by construction — the page must SAY so, or the record reads as a
    # cross-modality claim it isn't.
    assert "method" in r.text.lower()


def test_model_detail_shows_opponent_name_and_record():
    with SessionLocal() as db:
        _seed_h2h(db)

    r = client.get(f"/models/{PFX}-champ")
    assert r.status_code == 200
    assert "MdhChallengerModel" in r.text  # opponent resolved to a display name, not a raw id
    assert "3–1–0" in r.text  # W–L–T: ties surfaced, never silently dropped
    assert "75.0%" in r.text  # win rate straight from service (no contradicting rounding)


def test_model_detail_empty_state_when_no_games():
    """A never-compared model renders the empty state — not a crash, not a headless table."""
    with SessionLocal() as db:
        _gen(db, "lonely", "MdhLonelyModel")
        db.commit()

    r = client.get(f"/models/{PFX}-lonely")
    assert r.status_code == 200
    assert "Not enough head-to-head data yet." in r.text
    assert "<td" not in r.text.split("Head-to-head", 1)[1].split("</section>", 1)[0]
