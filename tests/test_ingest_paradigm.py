"""`register_output` must be able to put an entrant on the arena vote roster.

The arena vote pool is an allowlist over generator paradigm (config.ARENA_VOTE_PARADIGMS), so
a generator carrying a NULL paradigm is ingested, displayed, and then never served for voting.
Until this module existed, `register_output` had no `paradigm` parameter at all: every
generator whose first appearance was an ingest landed off-roster permanently, and
upsert_generator's warning told callers to "pass paradigm=" through a signature that offered
no such argument.

WHY THIS FILE EXISTS SEPARATELY FROM test_synthetic_plants_ingest.py

That test caught this and could not report it. It is skipif'd on the AgriGen bake-off GLBs at
an absolute path outside the repo, so it skips on every CI runner and fails only on a
workstation that happens to have them -- it skipped where it was checked and failed where it
ran. A regression invisible to the thing that runs on every commit is not guarded. Everything
here builds its own asset with trimesh, so it runs anywhere.
"""

from __future__ import annotations

import inspect

import pytest
import trimesh

from app import config, ingest
from app.database import SessionLocal, init_db
from app.models import Category, Generator, ModelOutput, Task


def setup_module(_m):
    init_db()


def _glb() -> bytes:
    return trimesh.creation.box().export(file_type="glb")


def _task(db, title: str) -> Task:
    cat = db.query(Category).filter_by(slug="plants").first() or Category(
        slug="plants", name="Plants"
    )
    db.add(cat)
    db.flush()
    task = Task(category_id=cat.id, title=title, prompt="p")
    db.add(task)
    db.flush()
    return task


def _votable() -> str:
    """A paradigm the arena actually serves, read from config rather than hardcoded.

    Hardcoding "image_recon" would keep passing if the roster were retuned to exclude it --
    the same silent-off-roster failure this module is about, one level up.
    """
    assert config.ARENA_VOTE_PARADIGMS, "no vote roster configured; these tests prove nothing"
    return sorted(config.ARENA_VOTE_PARADIGMS)[0]


def test_register_output_accepts_a_paradigm():
    """The API-level guard. The defect was a MISSING PARAMETER, so the signature is the thing
    to assert: the behavioural tests below would fail on TypeError if it were renamed, which
    reads as a broken test rather than as the roster regression it actually is."""
    sig = inspect.signature(ingest.register_output)
    assert "paradigm" in sig.parameters, (
        "register_output lost its paradigm parameter; ingested generators can no longer be "
        "put on the arena vote roster"
    )


def test_ingest_with_a_paradigm_lands_on_the_vote_roster():
    db = SessionLocal()
    try:
        task = _task(db, "Paradigm On Roster")
        want = _votable()
        ingest.register_output(
            db,
            task_id=task.id,
            generator_slug="paradigm-on-roster",
            data=_glb(),
            ext="glb",
            title="on roster",
            paradigm=want,
        )
        db.flush()
        gen = db.query(Generator).filter_by(slug="paradigm-on-roster").first()
        assert gen is not None
        assert gen.paradigm == want
        assert gen.paradigm in config.ARENA_VOTE_PARADIGMS
    finally:
        db.rollback()
        db.close()


def test_ingest_without_a_paradigm_is_still_off_roster():
    """The negative control, and deliberately NOT a bug report.

    Omitting the paradigm still produces an off-roster generator -- that is the documented
    contract (upsert_generator warns), not the regression. The regression was that callers had
    no way to opt IN. Asserting the off-roster case keeps the test above honest: if every
    ingest were on-roster regardless, that test would pass for a reason unrelated to the
    parameter it claims to exercise.
    """
    db = SessionLocal()
    try:
        task = _task(db, "Paradigm Off Roster")
        ingest.register_output(
            db,
            task_id=task.id,
            generator_slug="paradigm-off-roster",
            data=_glb(),
            ext="glb",
            title="off roster",
        )
        db.flush()
        gen = db.query(Generator).filter_by(slug="paradigm-off-roster").first()
        assert gen is not None
        assert not gen.paradigm
        assert gen.paradigm not in config.ARENA_VOTE_PARADIGMS
    finally:
        db.rollback()
        db.close()


def test_a_generator_born_blank_is_healed_by_a_later_ingest():
    """Parity with commission.get_or_create_generator and agentic, which both heal.

    Warning only at creation is useless to a row that already exists: it is already blank, the
    warning never fires again, and it stays off-roster forever. Every generator ingested before
    the paradigm parameter existed is in exactly that state, so without healing the fix would
    reach none of them.
    """
    db = SessionLocal()
    try:
        task = _task(db, "Paradigm Heal")
        ingest.register_output(
            db,
            task_id=task.id,
            generator_slug="paradigm-heal",
            data=_glb(),
            ext="glb",
            title="blank first",
        )
        db.flush()
        gen = db.query(Generator).filter_by(slug="paradigm-heal").first()
        assert not gen.paradigm, "precondition: this generator must start blank"

        want = _votable()
        ingest.upsert_generator(db, "paradigm-heal", paradigm=want)
        db.flush()
        db.refresh(gen)
        assert gen.paradigm == want
    finally:
        db.rollback()
        db.close()


def test_healing_never_overwrites_a_deliberate_paradigm():
    """The other half of the heal contract. A row that already states what it is must not be
    relabelled by whatever ingests onto it next -- otherwise one mis-tagged ingest silently
    moves an established generator to another board."""
    db = SessionLocal()
    try:
        roster = sorted(config.ARENA_VOTE_PARADIGMS)
        if len(roster) < 2:
            pytest.skip("need two distinct paradigms to prove one does not overwrite the other")
        first, second = roster[0], roster[1]

        ingest.upsert_generator(db, "paradigm-deliberate", paradigm=first)
        db.flush()
        ingest.upsert_generator(db, "paradigm-deliberate", paradigm=second)
        db.flush()

        gen = db.query(Generator).filter_by(slug="paradigm-deliberate").first()
        assert gen.paradigm == first, "an existing, deliberate paradigm was overwritten"
    finally:
        db.rollback()
        db.close()


def test_a_recognisable_slug_is_classified_without_the_caller_saying_anything():
    """The reason the ~19 existing call sites did not need editing.

    paradigms.classify_paradigm resolves plenty of slugs on keyword rules alone, so wiring it
    into register_output classifies those callers where they stand. The alternative -- a
    paradigm hardcoded at every call site -- would copy that rule table N times and let the
    copies drift, which is the failure this repo already has a canonical module to avoid.
    """
    db = SessionLocal()
    try:
        task = _task(db, "Classified By Slug")
        ingest.register_output(
            db,
            task_id=task.id,
            generator_slug="acme-trellis-v2",  # "trellis" -> image_recon, by slug alone
            data=_glb(),
            ext="glb",
            title="classified by slug",
        )
        db.flush()
        gen = db.query(Generator).filter_by(slug="acme-trellis-v2").first()
        assert gen.paradigm == "image_recon"
    finally:
        db.rollback()
        db.close()


def test_a_source_prefix_classifies_what_the_slug_cannot():
    """Source beats slug, and reaches slugs no keyword matches.

    Callers used to assign `out.source` AFTER register_output returned, so the classifier
    never saw it at the moment the generator was created. Passing it in is what makes the
    prefix rules ("api:text:", "procedural:", "found:", "scan:") usable at ingest.
    """
    db = SessionLocal()
    try:
        task = _task(db, "Classified By Source")
        ingest.register_output(
            db,
            task_id=task.id,
            generator_slug="acme-unknown-gen",  # no keyword rule matches this
            data=_glb(),
            ext="glb",
            title="classified by source",
            source="api:text:acme",
        )
        db.flush()
        gen = db.query(Generator).filter_by(slug="acme-unknown-gen").first()
        assert gen.paradigm == "text_native"
        out = db.query(ModelOutput).filter_by(generator_id=gen.id).first()
        assert out.source == "api:text:acme", "source must be persisted, not just classified"
    finally:
        db.rollback()
        db.close()


def test_an_explicit_paradigm_beats_the_classifier():
    """The override exists for slugs the rules resolve WRONGLY, so it has to win. If the
    classifier took precedence, a caller correcting a misclassification would have no way to
    do it short of renaming the generator."""
    db = SessionLocal()
    try:
        task = _task(db, "Explicit Beats Classifier")
        ingest.register_output(
            db,
            task_id=task.id,
            generator_slug="acme-trellis-v3",  # slug alone would say image_recon
            data=_glb(),
            ext="glb",
            title="explicit override",
            paradigm="text_native",
        )
        db.flush()
        gen = db.query(Generator).filter_by(slug="acme-trellis-v3").first()
        assert gen.paradigm == "text_native"
    finally:
        db.rollback()
        db.close()
