from __future__ import annotations

from app import paradigms


def test_vocabulary_and_validity():
    for p in ("image_recon", "capture_scan", "procedural_llm", "procedural_expert", "retrieval"):
        assert p in paradigms.PARADIGMS and p in paradigms.BACKFILL_PARADIGMS
    for p in ("text_native", "video", "texturing", "agentic", "sketch"):
        assert p in paradigms.PARADIGMS and p not in paradigms.BACKFILL_PARADIGMS
    assert paradigms.is_valid_paradigm("image_recon") is True
    assert paradigms.is_valid_paradigm("nope") is False


def test_display_names_cover_all():
    for p in paradigms.PARADIGMS:
        assert p in paradigms.DISPLAY_NAMES and paradigms.DISPLAY_NAMES[p]


def test_same_paradigm():
    assert paradigms.same_paradigm("image_recon", "image_recon") is True
    assert paradigms.same_paradigm("image_recon", "retrieval") is False
    # two empties (pre-backfill) count as same group; empty vs tagged does not
    assert paradigms.same_paradigm("", "") is True
    assert paradigms.same_paradigm("", "image_recon") is False
