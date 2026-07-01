import json
from sqlalchemy import delete
from scripts.export_public import export_bundle
from app.models import Generator
from app.storage import LocalStorageBackend

# reuse the _mk seed from test_public_export
from tests.test_public_export import _mk


def _clear_leaked_generators(db) -> None:
    """Cross-file leak guard (same class documented in tests/test_import_roundtrip.py's
    _clear_leaked_lpy_generator): a prior test module reseeding demo data via
    SessionLocal()+commit() on the shared suite engine can leave real, non-rolled-back
    Generator(slug="lpy") / Generator(slug="calibration") rows that collide with this
    file's own inserts of the same slugs. Scoped to this test's own rolled-back
    transaction."""
    db.execute(delete(Generator).where(Generator.slug.in_(["lpy", "calibration"])))
    db.flush()


def test_export_writes_bundle_and_no_agrigen_leak(db_session, tmp_path):
    e = _mk(db_session)
    # _mk()'s o_bad (external source, no license) shares o_ok's task/generator, so it is
    # unavoidably in the include-set resolved from task_titles=["maize-a"],
    # generator_slugs=["lpy"] below (resolve_include_ids has no per-output allowlist).
    # The fail-loud license gate (already covered by
    # test_public_export.py::test_check_licenses_fails_loud_on_unknown) would otherwise
    # abort this bundle-writing test before it reaches its own assertions, so give it a
    # redistributable license here -- this test exercises bundle serialization, not the gate.
    e["o_bad"].license = "CC-BY-4.0"
    db_session.flush()
    store = LocalStorageBackend(tmp_path / "src_assets")
    store.save("a.glb", b"GLBDATA-a")
    store.save("b.glb", b"GLBDATA-b")
    store.save("c.glb", b"GLBDATA-c")  # o_bad's asset_path; included now that its license is set above
    out = tmp_path / "bundle"
    manifest = export_bundle(
        db_session, store, task_titles=["maize-a"], generator_slugs=["lpy"], out_dir=out
    )
    rows = json.loads((out / "rows.json").read_text())
    assert "model_output" in rows and len(rows["model_output"]) >= 1
    assert (out / "assets" / "a.glb").read_bytes() == b"GLBDATA-a"
    # Leak assertions (Global Constraints):
    blob = (out / "rows.json").read_text() + json.dumps(manifest)
    assert "/home/mjarnold/agrigen" not in blob
    assert not list(out.rglob("*.npy"))
    assert manifest["sha256"]


def test_gold_pair_of_non_included_task_excluded(db_session, tmp_path):
    """A GoldPair whose task isn't in the export allowlist must not leak into the
    bundle -- its id, and its gold outputs, must be absent from rows.json (otherwise
    Task 4's importer sees a gold_pair.task_id / good_output_id / bad_output_id that
    doesn't resolve to any row it received)."""
    from app.models import Category, Generator, GoldPair, ModelOutput, Task

    e = _mk(db_session)
    e["o_bad"].license = "CC-BY-4.0"

    # Second task, NOT in the export allowlist (task_titles=["maize-a"] below), with its
    # own gold pair.
    t_other = Task(category_id=e["cat"].id, title="wheat-x", prompt="wheat", active=True)
    db_session.add(t_other)
    db_session.flush()
    o_good = ModelOutput(
        task_id=t_other.id,
        generator_id=e["g_ok"].id,
        asset_path="wheat-good.glb",
        source="bio3d-arena",
        license=None,
        is_gold=True,
    )
    o_decoy = ModelOutput(
        task_id=t_other.id,
        generator_id=e["g_ok"].id,
        asset_path="wheat-bad.glb",
        source="bio3d-arena",
        license=None,
        is_gold=True,
    )
    db_session.add_all([o_good, o_decoy])
    db_session.flush()
    gp = GoldPair(task_id=t_other.id, good_output_id=o_good.id, bad_output_id=o_decoy.id)
    db_session.add(gp)
    db_session.flush()

    store = LocalStorageBackend(tmp_path / "src_assets")
    store.save("a.glb", b"GLBDATA-a")
    store.save("b.glb", b"GLBDATA-b")
    store.save("c.glb", b"GLBDATA-c")
    out = tmp_path / "bundle"
    export_bundle(
        db_session, store, task_titles=["maize-a"], generator_slugs=["lpy"], out_dir=out
    )
    rows = json.loads((out / "rows.json").read_text())

    gold_pair_ids = {r["id"] for r in rows["gold_pair"]}
    assert gp.id not in gold_pair_ids

    output_ids = {r["id"] for r in rows["model_output"]}
    assert o_good.id not in output_ids
    assert o_decoy.id not in output_ids


def test_vote_of_excluded_comparison_excluded(db_session, tmp_path):
    """A Comparison referencing an output from a non-allowlisted generator must be
    dropped by referential completeness (it would dangle output_b_id), and any Vote on
    that comparison must be dropped with it (it would dangle comparison_id)."""
    from app.models import Comparison, Criterion, Generator, ModelOutput, Vote

    e = _mk(db_session)
    e["o_bad"].license = "CC-BY-4.0"

    crit = db_session.query(Criterion).filter_by(slug="overall").first() or Criterion(
        slug="overall", name="Overall"
    )
    db_session.add(crit)
    db_session.flush()

    # An output from a generator NOT in the export allowlist (generator_slugs=["lpy"]).
    g_other = Generator(slug="other-gen", name="Other", kind="model")
    db_session.add(g_other)
    db_session.flush()
    o_foreign = ModelOutput(
        task_id=e["t_pub"].id,
        generator_id=g_other.id,
        asset_path="foreign.glb",
        source="bio3d-arena",
        license=None,
    )
    db_session.add(o_foreign)
    db_session.flush()

    comp = Comparison(
        task_id=e["t_pub"].id,
        output_a_id=e["o_ok"].id,
        output_b_id=o_foreign.id,  # dangles: o_foreign's generator isn't allowlisted
        criterion_id=crit.id,
        session_id="sess-1",
    )
    db_session.add(comp)
    db_session.flush()
    vote = Vote(comparison_id=comp.id, winner="a", session_id="sess-1")
    db_session.add(vote)
    db_session.flush()

    store = LocalStorageBackend(tmp_path / "src_assets")
    store.save("a.glb", b"GLBDATA-a")
    store.save("b.glb", b"GLBDATA-b")
    store.save("c.glb", b"GLBDATA-c")
    out = tmp_path / "bundle"
    export_bundle(
        db_session, store, task_titles=["maize-a"], generator_slugs=["lpy"], out_dir=out
    )
    rows = json.loads((out / "rows.json").read_text())

    comparison_ids = {r["id"] for r in rows["comparison"]}
    assert comp.id not in comparison_ids

    vote_ids = {r["id"] for r in rows["vote"]}
    assert vote.id not in vote_ids
def test_export_ships_gold_calibration_generator_not_in_allowlist(db_session, tmp_path):
    """FIX 1: gold decoy outputs belong to the "calibration" generator (app/seed.py's
    _seed_gold), which a curator's generator_slugs allowlist will never include --
    resolve_include_ids only adds the gold outputs themselves to gold_output_ids, not
    their generator to inc.generator_ids. _filtered_rows must still ship the
    "calibration" generator row (referenced by generator_id on the shipped gold
    outputs), or those outputs dangle on import."""
    from app.models import GoldPair, ModelOutput

    _clear_leaked_generators(db_session)
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

    store = LocalStorageBackend(tmp_path / "src_assets")
    store.save("a.glb", b"GLBDATA-a")
    store.save("b.glb", b"GLBDATA-b")
    store.save("c.glb", b"GLBDATA-c")
    store.save("gold-good.glb", b"GLBDATA-gold-good")
    store.save("gold-bad.glb", b"GLBDATA-gold-bad")
    out = tmp_path / "bundle"
    # generator_slugs allowlist deliberately excludes "calibration".
    export_bundle(
        db_session, store, task_titles=["maize-a"], generator_slugs=["lpy"], out_dir=out
    )
    rows = json.loads((out / "rows.json").read_text())

    generator_ids = {r["id"] for r in rows["generator"]}
    assert calib.id in generator_ids  # else o_good/o_decoy's generator_id dangles

    output_ids = {r["id"] for r in rows["model_output"]}
    assert o_good.id in output_ids
    assert o_decoy.id in output_ids


def test_export_nulls_reference_asset_id_to_excluded_output(db_session, tmp_path):
    """FIX 2: Task.reference_asset_id is a nullable FK -> model_output.id. If it points
    to an output that didn't make the bundle, it must be nulled out rather than shipped
    dangling."""
    from app.models import Generator, ModelOutput

    e = _mk(db_session)
    e["o_bad"].license = "CC-BY-4.0"

    g_other = Generator(slug="other-gen", name="Other", kind="model")
    db_session.add(g_other)
    db_session.flush()
    o_foreign = ModelOutput(
        task_id=e["t_pub"].id,
        generator_id=g_other.id,  # not in generator_slugs allowlist below -> excluded
        asset_path="foreign.glb",
        source="bio3d-arena",
        license=None,
    )
    db_session.add(o_foreign)
    db_session.flush()
    e["t_pub"].reference_asset_id = o_foreign.id
    db_session.flush()

    store = LocalStorageBackend(tmp_path / "src_assets")
    store.save("a.glb", b"GLBDATA-a")
    store.save("b.glb", b"GLBDATA-b")
    store.save("c.glb", b"GLBDATA-c")
    out = tmp_path / "bundle"
    export_bundle(
        db_session, store, task_titles=["maize-a"], generator_slugs=["lpy"], out_dir=out
    )
    rows = json.loads((out / "rows.json").read_text())

    output_ids = {r["id"] for r in rows["model_output"]}
    assert o_foreign.id not in output_ids  # confirms the FK would otherwise dangle

    t_rows = {r["id"]: r for r in rows["task"]}
    assert t_rows[e["t_pub"].id]["reference_asset_id"] is None
