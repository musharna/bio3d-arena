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


def test_classify_text_to_3d_is_text_native_not_image_recon():
    # Text→3D outputs are tagged api:text:<slug>; they must classify as text_native, NOT
    # image_recon (the generic api: rule) — the paradigm distinction is text prompt vs image input.
    assert classify("fal:tripo-p1-text", "model", {"api:text:fal:tripo-p1-text"}) == "text_native"
    assert (
        classify("fal:hunyuan3d-v3-text", "model", {"api:text:fal:hunyuan3d-v3-text"})
        == "text_native"
    )
    # Regression: a plain image→3D api: source is still image_recon.
    assert classify("fal:trellis", "model", {"api:fal:trellis"}) == "image_recon"


def test_classify_agentic_source_is_agentic():
    assert classify("agentic-openai-gpt-5-1", "model", {"agentic:openai/gpt-5.1"}) == "agentic"
    # regression: image/text api sources unchanged
    assert classify("fal:trellis", "model", {"api:fal:trellis"}) == "image_recon"
    assert classify("fal:tripo-p1-text", "model", {"api:text:fal:tripo-p1-text"}) == "text_native"


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
