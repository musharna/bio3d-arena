# tests/test_export_gold_leak.py
"""Task 6: gold-pair outputs must be gated by the TRUE provenance of the underlying asset
they alias (same asset_path), not by their own bio3d-arena/None decoy metadata. Otherwise a
commercial/NC/hard-exclude asset's bytes ship via its gold "good" copy, unseen by
check_licenses (gold ids never pass through inc.output_ids)."""

import uuid

from app.database import SessionLocal, init_db
from app.models import Generator, ModelOutput, Task
from app.public_export import IncludeSet, effective_provenance, filter_gold_for_posture


def setup_module(_m):
    init_db()


def _gen(db, kind="model"):
    g = Generator(slug=f"g-{uuid.uuid4().hex}", name="g", kind=kind, paradigm="p")
    db.add(g)
    db.flush()
    return g


def _task(db):
    t = Task(title=f"t-{uuid.uuid4().hex[:8]}", prompt="p", category_id=1)
    db.add(t)
    db.flush()
    return t


def _output(db, task, gen, *, asset_path, source, license_=None, is_gold=False):
    o = ModelOutput(
        task_id=task.id,
        generator_id=gen.id,
        asset_path=asset_path,
        asset_format="glb",
        source=source,
        license=license_,
        is_gold=is_gold,
    )
    db.add(o)
    db.flush()
    return o


def test_effective_provenance_gold_resolves_to_underlying_asset():
    with SessionLocal() as db:
        t = _task(db)
        comm = _output(
            db, t, _gen(db), asset_path="c.glb", source="api:fal:trellis", license_="terms"
        )
        gold = _output(
            db, t, _gen(db, "decoy"), asset_path="c.glb", source="bio3d-arena", is_gold=True
        )
        assert effective_provenance(db, gold) == ("api:fal:trellis", "terms")
        # A non-gold output's effective provenance is always its own.
        assert effective_provenance(db, comm) == ("api:fal:trellis", "terms")
        db.rollback()


def test_effective_provenance_gold_falls_back_to_own_when_no_match():
    with SessionLocal() as db:
        t = _task(db)
        gold = _output(
            db,
            t,
            _gen(db, "decoy"),
            asset_path=f"orphan-{uuid.uuid4().hex}.glb",
            source="bio3d-arena",
            is_gold=True,
        )
        assert effective_provenance(db, gold) == ("bio3d-arena", None)
        db.rollback()


def test_gold_commercial_alias_dropped_redistribute_kept_display():
    with SessionLocal() as db:
        t = _task(db)
        _output(db, t, _gen(db), asset_path="c.glb", source="api:fal:trellis", license_="terms")
        gold = _output(
            db, t, _gen(db, "decoy"), asset_path="c.glb", source="bio3d-arena", is_gold=True
        )
        inc = IncludeSet(gold_output_ids={gold.id})
        filter_gold_for_posture(db, inc, "redistribute", gated=set())
        assert inc.gold_output_ids == set()

        inc2 = IncludeSet(gold_output_ids={gold.id})
        filter_gold_for_posture(db, inc2, "display", gated=set())
        assert inc2.gold_output_ids == {gold.id}
        db.rollback()


def test_gold_hard_exclude_alias_dropped_both_postures():
    with SessionLocal() as db:
        t = _task(db)
        _output(
            db,
            t,
            _gen(db),
            asset_path="xf.glb",
            source="found:xfrog",
            license_="XfrogPlants commercial",
        )
        gold = _output(
            db, t, _gen(db, "decoy"), asset_path="xf.glb", source="bio3d-arena", is_gold=True
        )
        for posture in ("redistribute", "display"):
            inc = IncludeSet(gold_output_ids={gold.id})
            filter_gold_for_posture(db, inc, posture, gated=set())
            assert inc.gold_output_ids == set(), posture
        db.rollback()


def test_gold_cc_alias_kept_both_postures():
    with SessionLocal() as db:
        t = _task(db)
        _output(db, t, _gen(db), asset_path="cc.glb", source="plant3d", license_="CC0-1.0")
        gold = _output(
            db, t, _gen(db, "decoy"), asset_path="cc.glb", source="bio3d-arena", is_gold=True
        )
        for posture in ("redistribute", "display"):
            inc = IncludeSet(gold_output_ids={gold.id})
            filter_gold_for_posture(db, inc, posture, gated=set())
            assert inc.gold_output_ids == {gold.id}, posture
        db.rollback()


def test_gold_gated_dropped_both_postures():
    with SessionLocal() as db:
        t = _task(db)
        _output(db, t, _gen(db), asset_path="g.glb", source="plant3d", license_="CC0-1.0")
        gold = _output(
            db, t, _gen(db, "decoy"), asset_path="g.glb", source="bio3d-arena", is_gold=True
        )
        for posture in ("redistribute", "display"):
            inc = IncludeSet(gold_output_ids={gold.id})
            filter_gold_for_posture(db, inc, posture, gated={gold.id})
            assert inc.gold_output_ids == set(), posture
        db.rollback()


def test_gold_no_matching_underlying_falls_back_to_own_and_is_kept():
    with SessionLocal() as db:
        t = _task(db)
        gold = _output(
            db,
            t,
            _gen(db, "decoy"),
            asset_path=f"orphan-{uuid.uuid4().hex}.glb",
            source="bio3d-arena",
            is_gold=True,
        )
        for posture in ("redistribute", "display"):
            inc = IncludeSet(gold_output_ids={gold.id})
            filter_gold_for_posture(db, inc, posture, gated=set())
            assert inc.gold_output_ids == {gold.id}, posture
        db.rollback()
