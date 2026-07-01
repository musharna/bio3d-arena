from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session
from app.models import Generator, GoldPair, ModelOutput, Task
from app.storage import LocalStorageBackend
from scripts.export_public import export_bundle
from scripts.import_public import import_bundle, BundleChecksumError
from tests.test_public_export import _mk
import pytest


def _clear_leaked_lpy_generator(db) -> None:
    """Pre-existing cross-file leak, unrelated to import_public.py: tests/test_generate_lpy.py
    creates a real, non-rolled-back Generator(slug="lpy") via SessionLocal()+commit() on the
    shared suite engine (app.database.engine). If that test module runs earlier in this
    process (alphabetically before tests/test_ingest.py, whose setup_module() reseeds and
    incidentally clears it), the leaked row survives into this file's db_session transaction
    and collides with _mk()'s own unconditional Generator(slug="lpy") insert. Defensively
    clear it (scoped to this test's own rolled-back transaction) so this test's outcome
    doesn't depend on suite ordering. Reproduced without any of this file's code via:
    `pytest tests/test_generate_lpy.py tests/test_public_export.py`.

    Same leak class applies to slug="calibration": app.seed._seed_gold (invoked by any
    test that reseeds demo data via SessionLocal()+commit(), e.g. tests/test_ingest.py's
    setup_module()) also unconditionally inserts Generator(slug="calibration"), which
    collides with this file's own gold-pair tests that insert that slug.
    """
    db.execute(delete(Generator).where(Generator.slug.in_(["lpy", "calibration"])))
    db.flush()


def test_roundtrip_matches_and_no_leak(db_session, tmp_path):
    _clear_leaked_lpy_generator(db_session)
    e = _mk(db_session)
    # _mk()'s o_bad (external source, no license) shares o_ok's task/generator, so it is
    # unavoidably in the include-set for task_titles=["maize-a"], generator_slugs=["lpy"]
    # below. The fail-loud license gate is already covered by
    # test_public_export.py::test_check_licenses_fails_loud_on_unknown, so neutralize it
    # here (same pattern as test_export_script.py) -- this test exercises the import
    # round-trip, not the license gate.
    e["o_bad"].license = "CC-BY-4.0"
    db_session.flush()
    src = LocalStorageBackend(tmp_path / "src")
    src.save("a.glb", b"A")
    src.save("b.glb", b"B")
    src.save("c.glb", b"C")  # o_bad's asset_path
    out = tmp_path / "bundle"
    export_bundle(db_session, src, task_titles=["maize-a"], generator_slugs=["lpy"], out_dir=out)

    dst_url = f"sqlite:///{tmp_path / 'public.db'}"
    dst_store = LocalStorageBackend(tmp_path / "dst")
    counts = import_bundle(out, database_url=dst_url, storage=dst_store)

    eng = create_engine(dst_url)
    with Session(eng) as s:
        assert s.execute(select(Task)).scalars().all()  # tasks landed
        outs = s.execute(select(ModelOutput)).scalars().all()
        assert outs and all(dst_store.exists(o.asset_path) for o in outs)
    # leak grep over the whole bundle tree
    for p in out.rglob("*"):
        if p.is_file():
            assert "/home/mjarnold/agrigen" not in p.read_bytes().decode("utf-8", "ignore")
    assert not list(out.rglob("*.npy"))


def test_import_rejects_tampered_bundle(db_session, tmp_path):
    _clear_leaked_lpy_generator(db_session)
    e = _mk(db_session)
    e["o_bad"].license = "CC-BY-4.0"  # neutralize license gate; see comment above
    db_session.flush()
    src = LocalStorageBackend(tmp_path / "src")
    src.save("a.glb", b"A")
    src.save("b.glb", b"B")
    src.save("c.glb", b"C")
    out = tmp_path / "bundle"
    export_bundle(db_session, src, task_titles=["maize-a"], generator_slugs=["lpy"], out_dir=out)
    (out / "rows.json").write_bytes(b'{"tampered": []}')
    with pytest.raises(BundleChecksumError):
        import_bundle(
            out,
            database_url=f"sqlite:///{tmp_path / 'p.db'}",
            storage=LocalStorageBackend(tmp_path / "dst"),
        )
def test_roundtrip_imports_gold_pair_with_calibration_generator(db_session, tmp_path):
    """FIX 1 end-to-end: a gold pair's outputs belong to the "calibration" generator,
    which the curator's generator_slugs allowlist doesn't include. The exported bundle
    must still ship that generator (referential completeness in _filtered_rows), so the
    import lands both gold outputs with a resolvable generator_id -- no dangling FK."""
    _clear_leaked_lpy_generator(db_session)
    e = _mk(db_session)
    e["o_bad"].license = "CC-BY-4.0"

    calib = Generator(slug="calibration", name="Calibration (gold)", kind="decoy", is_anonymous=True)
    db_session.add(calib)
    db_session.flush()
    o_good = ModelOutput(
        task_id=e["t_pub"].id,
        generator_id=calib.id,
        asset_path="gold-good.glb",
        source="bio3d-arena",
        license=None,
        is_gold=True,
    )
    o_decoy = ModelOutput(
        task_id=e["t_pub"].id,
        generator_id=calib.id,
        asset_path="gold-bad.glb",
        source="bio3d-arena",
        license=None,
        is_gold=True,
    )
    db_session.add_all([o_good, o_decoy])
    db_session.flush()
    gp = GoldPair(task_id=e["t_pub"].id, good_output_id=o_good.id, bad_output_id=o_decoy.id)
    db_session.add(gp)
    db_session.flush()

    src = LocalStorageBackend(tmp_path / "src")
    src.save("a.glb", b"A")
    src.save("b.glb", b"B")
    src.save("c.glb", b"C")
    src.save("gold-good.glb", b"GOOD")
    src.save("gold-bad.glb", b"BAD")
    out = tmp_path / "bundle"
    # generator_slugs allowlist deliberately excludes "calibration".
    export_bundle(db_session, src, task_titles=["maize-a"], generator_slugs=["lpy"], out_dir=out)

    dst_url = f"sqlite:///{tmp_path / 'public.db'}"
    dst_store = LocalStorageBackend(tmp_path / "dst")
    import_bundle(out, database_url=dst_url, storage=dst_store)  # must not raise (no dangling FK)

    eng = create_engine(dst_url)
    with Session(eng) as s:
        gen_ids = {g.id for g in s.execute(select(Generator)).scalars()}
        outs = {o.id: o for o in s.execute(select(ModelOutput)).scalars()}
        assert o_good.id in outs and o_decoy.id in outs
        # Every imported gold output's generator_id resolves to an imported generator row.
        assert outs[o_good.id].generator_id in gen_ids
        assert outs[o_decoy.id].generator_id in gen_ids
        assert outs[o_good.id].generator_id == calib.id
