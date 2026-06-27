from __future__ import annotations

from app.database import SessionLocal, init_db

from scripts.difficulty_report import build_report


def setup_module(_m):
    init_db()


def test_report_has_a_section_per_tier_with_header_and_empty_placeholder():
    with SessionLocal() as db:
        text = build_report(db)
    for tier in ("easy", "moderate", "hard", "untiered"):
        assert f"## Tier: {tier}" in text
    assert "| generator |" in text  # header row present
    # A tier with no rows renders the honest placeholder (em-dash only appears once a
    # tier has scored-but-partial rows, which this empty-DB report does not seed).
    assert "_(no tasks in this tier)_" in text
