import json
from pathlib import Path
from app.storage import LocalStorageBackend
from scripts.build_dataset_release import build_release
from tests.test_public_export import _mk


def test_build_release_decorates_bundle_and_no_leak(db_session, tmp_path):
    e = _mk(db_session)
    # _mk()'s o_bad (external source, no license) shares o_ok's task/generator, so it is
    # unavoidably in the include-set resolved from task_titles=["maize-a"],
    # generator_slugs=["lpy"] below (resolve_include_ids has no per-output allowlist). The
    # fail-loud license gate would otherwise abort build_release before it reaches its own
    # assertions, so give it a redistributable license here -- same fix as
    # tests/test_export_script.py::test_export_writes_bundle_and_no_agrigen_leak and
    # tests/test_import_roundtrip.py, which hit the identical _mk() include-set quirk.
    e["o_bad"].license = "CC-BY-4.0"
    db_session.flush()
    store = LocalStorageBackend(tmp_path / "src")
    store.save("a.glb", b"A")
    store.save("b.glb", b"B")
    store.save("c.glb", b"C")  # o_bad's asset_path; included now that its license is set above
    out = tmp_path / "releases" / "2026.07-v1"
    summary = build_release(
        db_session,
        store,
        version="2026.07-v1",
        task_titles=["maize-a"],
        generator_slugs=["lpy"],
        out_dir=out,
    )
    assert (out / "LICENSE").exists()
    assert "2026.07-v1" in (out / "VERSION").read_text()
    assert summary["sha256"] in (out / "VERSION").read_text()
    assert (out / "DATASHEET.md").exists()
    # NOTE: db_session shares a temp DB with OTHER test modules' setup_module-level commits
    # (seed_all + real votes), which persist outside this test's rollback-isolated transaction.
    # So on full-suite runs n_votes may be > 0 even though this test's own seed (_mk) adds no
    # votes. Assert structure/non-negativity, not an exact count -- same >=-not-== convention
    # as tests/test_dataset_helpers.py::test_build_preference_records_shape and
    # tests/test_research.py's export assertion.
    prefs = json.loads((out / "preference_records.json").read_text())
    assert isinstance(prefs["n_votes"], int) and prefs["n_votes"] >= 0
    assert prefs["n_votes"] == len(prefs["votes"])
    assert (out / "bundle" / "rows.json").exists()
    # leak assertions over the whole release tree
    assert not list(out.rglob("*.npy"))
    for p in out.rglob("*"):
        if p.is_file():
            assert "/home/mjarnold/agrigen" not in p.read_bytes().decode("utf-8", "ignore")
    assert summary["tarball"].endswith(".tar.gz") and Path(summary["tarball"]).exists()


def test_preference_records_scoped_to_release_bundle(db_session, tmp_path):
    """FIX 1: preference_records.json must be scoped to the release bundle's own comparisons,
    not the full unfiltered global vote log (app.dataset.build_preference_records's default,
    which is what /api/export.json ships). A vote on a comparison belonging to a task NOT in
    the release's task_titles allowlist must not appear -- otherwise a scoped release would
    ship votes referencing excluded tasks/generators and dangling asset_a/asset_b paths."""
    from sqlalchemy import select

    from app.models import Comparison, Criterion, Generator, ModelOutput, Task, Vote

    e = _mk(db_session)
    e["o_bad"].license = "CC-BY-4.0"
    db_session.flush()

    crit = (
        db_session.execute(select(Criterion).where(Criterion.slug == "overall")).scalars().first()
    )
    if crit is None:
        crit = Criterion(slug="overall", name="Overall")
        db_session.add(crit)
        db_session.flush()

    # Included comparison+vote: on the allowlisted task/generator (maize-a / lpy).
    comp_included = Comparison(
        task_id=e["t_pub"].id,
        output_a_id=e["o_ok"].id,
        output_b_id=e["o_self"].id,
        criterion_id=crit.id,
        session_id="s-inc",
    )
    db_session.add(comp_included)
    db_session.flush()
    db_session.add(Vote(comparison_id=comp_included.id, winner="a", session_id="s-inc"))
    db_session.flush()

    # Excluded comparison+vote: a SECOND task, NOT in the release's task_titles=["maize-a"]
    # allowlist below, with its own generator + outputs + comparison + vote.
    t_other = Task(category_id=e["cat"].id, title="wheat-x", prompt="wheat", active=True)
    db_session.add(t_other)
    db_session.flush()
    g_other = Generator(slug="other-gen", name="Other", kind="model")
    db_session.add(g_other)
    db_session.flush()
    o_x1 = ModelOutput(
        task_id=t_other.id,
        generator_id=g_other.id,
        asset_path="x1.glb",
        source="bio3d-arena",
        license=None,
    )
    o_x2 = ModelOutput(
        task_id=t_other.id,
        generator_id=g_other.id,
        asset_path="x2.glb",
        source="bio3d-arena",
        license=None,
    )
    db_session.add_all([o_x1, o_x2])
    db_session.flush()
    comp_excluded = Comparison(
        task_id=t_other.id,
        output_a_id=o_x1.id,
        output_b_id=o_x2.id,
        criterion_id=crit.id,
        session_id="s-exc",
    )
    db_session.add(comp_excluded)
    db_session.flush()
    db_session.add(Vote(comparison_id=comp_excluded.id, winner="b", session_id="s-exc"))
    db_session.flush()

    store = LocalStorageBackend(tmp_path / "src2")
    store.save("a.glb", b"A")
    store.save("b.glb", b"B")
    store.save("c.glb", b"C")
    out = tmp_path / "releases" / "2026.07-v2"
    build_release(
        db_session,
        store,
        version="2026.07-v2",
        task_titles=["maize-a"],
        generator_slugs=["lpy"],
        out_dir=out,
    )

    prefs = json.loads((out / "preference_records.json").read_text())
    comp_ids_present = {v["comparison_id"] for v in prefs["votes"]}
    assert comp_included.id in comp_ids_present
    assert comp_excluded.id not in comp_ids_present
