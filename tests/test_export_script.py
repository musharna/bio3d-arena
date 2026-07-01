import json
from scripts.export_public import export_bundle
from app.storage import LocalStorageBackend

# reuse the _mk seed from test_public_export
from tests.test_public_export import _mk


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
