from __future__ import annotations


from scripts.backfill_paradigms import classify


def test_classify_each_family():
    assert classify("openrouter-anthropic-claude-opus-4-8", "model", set()) == "procedural_llm"
    assert classify("lpy-maize", "model", {"bio3d-arena"}) == "procedural_expert"
    assert classify("infinigen-rose", "model", set()) == "procedural_expert"
    assert classify("hunyuan3d", "model", {"api:hunyuan"}) == "image_recon"
    assert classify("g1", "model", {"api:tripo"}) == "image_recon"
    assert classify("icrisat-sorghum", "model", {"icrisat"}) == "capture_scan"
    assert classify("sketchfab-rose", "model", {"bio3d-arena"}) == "retrieval"
    assert classify("objaverse-plant", "model", set()) == "retrieval"


def test_classify_unknown_returns_none():
    assert classify("totally-unknown-gen", "model", {"mystery"}) is None


def test_classify_source_prefix_families():
    assert classify("agrigen", "model", {"procedural:agrigen"}) == "procedural_expert"
    assert classify("helios", "model", {"procedural:helios"}) == "procedural_expert"
    assert classify("xfrog-a", "model", {"found:xfrog"}) == "retrieval"
    assert classify("ct:rose-x", "model", {"ct:rose-x"}) == "capture_scan"
    assert classify("mri:barley", "model", {"mri:ipk"}) == "capture_scan"
    assert classify("instantmesh", "model", {"bio3d-arena"}) == "image_recon"


def test_assign_skips_decoy_and_zero_output(tmp_path):
    from app.database import SessionLocal, init_db
    from app.models import Generator
    from scripts.backfill_paradigms import assign_paradigms

    init_db()
    with SessionLocal() as db:
        d = Generator(slug="decoy-x", name="d", kind="decoy")
        z = Generator(slug="zero-x", name="z", kind="model")  # 0 outputs, unclassifiable
        db.add_all([d, z])
        db.commit()
        res = assign_paradigms(db, commit=False)
        assert "decoy-x" in res["skipped"] and "zero-x" in res["skipped"]
        assert "decoy-x" not in res["unmapped"] and "zero-x" not in res["unmapped"]
        db.delete(d)
        db.delete(z)
        db.commit()
