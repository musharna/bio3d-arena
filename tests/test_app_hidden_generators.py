"""AgriGen's internal procedural-expert testers (agrigen / demeter / helios) are kept in the DB
for internal analysis but must NOT surface anywhere in the app UI — excluded from the perceptual
boards (via mode_a_excluded_generator_ids) and from the arena vote pool. This is separate from the
public-export gate (which also drops them): it hides them on every instance, public or internal."""

import uuid

from app import config, service
from app.database import SessionLocal, init_db
from app.models import Generator


def setup_module(_m):
    init_db()


def _gen(db, slug, paradigm="procedural_expert"):
    g = Generator(slug=slug, name=slug.title(), kind="model", paradigm=paradigm)
    db.add(g)
    db.flush()
    return g


def test_app_hidden_slugs_cover_the_agrigen_testers():
    assert {"agrigen", "demeter", "helios"} <= set(config.APP_HIDDEN_GENERATOR_SLUGS)


def test_app_hidden_generator_ids_resolves_seeded_testers():
    with SessionLocal() as db:
        ag = _gen(db, "agrigen")
        dm = _gen(db, "demeter")
        keep = _gen(db, f"trellis-{uuid.uuid4().hex[:6]}", paradigm="image_recon")
        hidden = service.app_hidden_generator_ids(db)
        assert ag.id in hidden and dm.id in hidden
        assert keep.id not in hidden
        db.rollback()


def test_app_hidden_generators_excluded_from_perceptual_boards():
    with SessionLocal() as db:
        dm = _gen(db, "demeter")
        hl = _gen(db, "helios")
        keep = _gen(db, f"trellis-{uuid.uuid4().hex[:6]}", paradigm="image_recon")
        excluded = service.mode_a_excluded_generator_ids(db)
        assert dm.id in excluded and hl.id in excluded
        assert keep.id not in excluded
        db.rollback()
