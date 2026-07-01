from __future__ import annotations


from scripts.backfill_paradigms import classify


def test_classify_each_family():
    assert classify("openrouter-anthropic-claude-opus-4-8", "model", set()) == "procedural_llm"
    assert classify("lpy-maize", "model", {"bio3d-arena"}) == "procedural_expert"
    assert classify("infinigen-rose", "model", set()) == "procedural_expert"
    assert classify("hunyuan3d", "model", {"api:hunyuan"}) == "image_recon"
    assert classify("g1", "model", {"api:tripo"}) == "image_recon"
    assert classify("icrisat-sorghum", "model", {"icrisat"}) == "capture_scan"
    assert classify("g2", "model", {"sketchfab"}) == "retrieval"
    assert classify("g3", "model", {"objaverse"}) == "retrieval"


def test_classify_unknown_returns_none():
    assert classify("totally-unknown-gen", "model", {"mystery"}) is None
