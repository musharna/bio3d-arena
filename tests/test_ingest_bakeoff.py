"""Bake-off filename parsing — v1 (<species>__<method>) and P1 error-bar harvest
(<species>__<method>__<photo_id>, N photos/species → N distinct recons per method)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "ingest_bakeoff", Path(__file__).resolve().parent.parent / "scripts" / "ingest_bakeoff.py"
)
ingest_bakeoff = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ingest_bakeoff)
parse = ingest_bakeoff.parse_bakeoff_name


def test_v1_two_part_name():
    assert parse("arabidopsis_thaliana__trellis") == ("arabidopsis_thaliana", "trellis", None)


def test_p1_three_part_name_keeps_method_clean():
    # the photo_id must NOT glue onto the method (the split("__", 1) bug)
    assert parse("arabidopsis_thaliana__trellis__03") == (
        "arabidopsis_thaliana",
        "trellis",
        "03",
    )


def test_species_slug_underscores_preserved():
    assert parse("solanum_lycopersicum__hunyuan3d__photo5") == (
        "solanum_lycopersicum",
        "hunyuan3d",
        "photo5",
    )


def test_malformed_name_returns_none():
    assert parse("nosep") is None
    assert parse("a__") is None  # empty method
