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
