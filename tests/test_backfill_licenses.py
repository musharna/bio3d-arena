# tests/test_backfill_licenses.py
import json
import sys
import uuid

import pytest

import scripts.backfill_licenses as backfill_mod
from scripts.backfill_licenses import CC0, backfill_licenses, main
from app.database import SessionLocal, init_db
from app.models import Generator, ModelOutput, Task


def setup_module(_m):
    init_db()


def _out(db, source, license_=None, meta=None):
    g = Generator(slug=f"g-{uuid.uuid4().hex}", name="g", kind="model", paradigm="p")
    db.add(g)
    db.flush()
    t = Task(title=f"t-{uuid.uuid4().hex[:8]}", prompt="p", category_id=1)
    db.add(t)
    db.flush()
    o = ModelOutput(
        task_id=t.id,
        generator_id=g.id,
        asset_path="x.glb",
        asset_format="glb",
        source=source,
        license=license_,
        meta_json=json.dumps(meta or {}),
    )
    db.add(o)
    db.flush()
    return o


def test_own_llm_procedural_get_cc0():
    with SessionLocal() as db:
        own = _out(db, "bio3d-arena")
        comm = _out(db, "commissioned")
        agent = _out(db, "agentic:openai/gpt-5.1")
        proc = _out(db, "procedural:lpy", license_="L-Py (CeCILL-C)")
        backfill_licenses(db, objaverse_license_for=lambda uid: None)
        for o in (own, comm, agent, proc):
            assert o.license == CC0
        db.rollback()


def test_crops3d_left_nonredistributable():
    """crops3d must NOT be force-relabelled to CC0: no source supports CC0 (Figshare dataset
    reads CC-BY-4.0, the Sci Data article boilerplate reads CC-BY-NC-ND). It falls through to
    normalize-only, keeping a non-allowlisted license so the export gate excludes it until the
    NC-ND/CC-BY ambiguity is resolved by counsel."""
    with SessionLocal() as db:
        c = _out(db, "crops3d", license_="CC-BY-NC-ND 4.0")
        backfill_licenses(db, objaverse_license_for=lambda uid: None)
        assert c.license != CC0
        assert c.license == "CC-BY-NC-ND-4.0"  # normalized only, stays gate-excluded
        db.rollback()


def test_objaverse_per_uid_and_nc_left_nonredistributable():
    with SessionLocal() as db:
        keep = _out(db, "objaverse", license_="by", meta={"objaverse_uid": "AAA"})
        drop = _out(db, "objaverse", license_="by", meta={"objaverse_uid": "BBB"})
        lookup = {"AAA": "by", "BBB": "by-nc"}
        backfill_licenses(db, objaverse_license_for=lambda uid: lookup.get(uid))
        assert keep.license == "CC-BY-4.0"
        assert (
            drop.license == "CC-BY-NC-4.0"
        )  # normalized but stays non-allowlisted -> gate excludes
        db.rollback()


def test_hard_excludes_untouched():
    with SessionLocal() as db:
        xf = _out(db, "found:xfrog", license_="XfrogPlants commercial (purchased)")
        dm = _out(db, "procedural:demeter", license_="Demeter (NC research)")
        before = (xf.license, dm.license)
        backfill_licenses(db, objaverse_license_for=lambda uid: None)
        assert (xf.license, dm.license) == before  # never relabelled
        db.rollback()


def test_idempotent():
    with SessionLocal() as db:
        o = _out(db, "bio3d-arena")
        backfill_licenses(db, objaverse_license_for=lambda uid: None)
        first = o.license
        backfill_licenses(db, objaverse_license_for=lambda uid: None)
        assert o.license == first
        db.rollback()


def test_main_refuses_unsafe_db(monkeypatch):
    """main()'s DB-safety guard must refuse a real-looking (non-copy) DB target."""
    monkeypatch.setattr(
        backfill_mod.config, "DATABASE_URL", "sqlite:////srv/bio3d/data/arena-prod.db"
    )
    monkeypatch.setattr(sys, "argv", ["backfill_licenses.py"])
    with pytest.raises(SystemExit):
        main()
