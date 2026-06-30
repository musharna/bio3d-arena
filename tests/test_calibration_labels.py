"""Tests for the Mode-C human-calibration CSV round-trip (scripts/calibration_labels.py).

Covers the two pure seams: stratified blind sampling (export) and filled-CSV parsing
(ingest). The DB-touching wiring (export query, recompute call) is exercised by the
real-execution round-trip in the session, not here."""

from __future__ import annotations

import pytest

from scripts.calibration_labels import (
    VOCAB,
    is_judgeable,
    parse_label_rows,
    stratified_sample,
)


def test_is_judgeable_rejects_unstated():
    assert is_judgeable("globose, red") is True
    assert is_judgeable("low variation (less than expected)") is True  # weak but checkable
    assert is_judgeable("not explicitly stated") is False
    assert is_judgeable("") is False
    assert is_judgeable("  Not Explicitly Stated  ") is False
    assert is_judgeable("unknown") is False


def _v(oid, key, cls, verdict):
    return {
        "output_id": oid,
        "trait_key": key,
        "trait_class": cls,
        "vlm_verdict": verdict,
        "taxon": "Zea mays",
        "expected": "x",
        "visual": "y",
        "contact_sheet": f"/r/{oid}.png",
    }


def test_stratified_sample_caps_per_class():
    verdicts = [_v(i, f"k{i}", "color", "absent") for i in range(50)]
    out = stratified_sample(verdicts, per_class=10, classes=None, seed=1)
    assert len(out) == 10
    assert all(r["trait_class"] == "color" for r in out)


def test_stratified_sample_balances_across_verdicts():
    # 30 absent + 6 present_correct; a balanced draw must pull some present_correct,
    # not just the dominant bucket — otherwise kappa is degenerate.
    verdicts = [_v(i, f"a{i}", "color", "absent") for i in range(30)]
    verdicts += [_v(100 + i, f"p{i}", "color", "present_correct") for i in range(6)]
    out = stratified_sample(verdicts, per_class=10, classes=None, seed=7)
    picked = {r["vlm_verdict"] for r in out}
    assert "present_correct" in picked
    assert "absent" in picked


def test_stratified_sample_respects_classes_filter():
    verdicts = [_v(i, f"k{i}", "color", "absent") for i in range(5)]
    verdicts += [_v(50 + i, f"h{i}", "habit", "absent") for i in range(5)]
    out = stratified_sample(verdicts, per_class=10, classes={"habit"}, seed=1)
    assert {r["trait_class"] for r in out} == {"habit"}


def test_stratified_sample_deterministic():
    verdicts = [_v(i, f"k{i}", "color", "absent" if i % 2 else "present_wrong") for i in range(40)]
    a = stratified_sample(verdicts, per_class=12, classes=None, seed=99)
    b = stratified_sample(verdicts, per_class=12, classes=None, seed=99)
    assert [r["trait_key"] for r in a] == [r["trait_key"] for r in b]


def test_stratified_sample_fewer_available_than_requested():
    verdicts = [_v(i, f"k{i}", "phyllotaxy", "absent") for i in range(3)]
    out = stratified_sample(verdicts, per_class=10, classes=None, seed=1)
    assert len(out) == 3


def test_parse_label_rows_valid():
    rows = [
        {
            "output_id": "12",
            "trait_key": "leaf_shape",
            "trait_class": "organ_shape",
            "human_verdict": "present_correct",
        },
        {
            "output_id": "13",
            "trait_key": "habit",
            "trait_class": "habit",
            "human_verdict": "absent",
        },
    ]
    labels, skipped = parse_label_rows(rows)
    assert labels == [
        (12, "leaf_shape", "organ_shape", "present_correct"),
        (13, "habit", "habit", "absent"),
    ]
    assert skipped == 0


def test_parse_label_rows_skips_blank():
    rows = [
        {"output_id": "12", "trait_key": "k", "trait_class": "color", "human_verdict": ""},
        {"output_id": "13", "trait_key": "k", "trait_class": "color", "human_verdict": "  "},
        {"output_id": "14", "trait_key": "k", "trait_class": "color", "human_verdict": "absent"},
    ]
    labels, skipped = parse_label_rows(rows)
    assert len(labels) == 1
    assert skipped == 2


def test_parse_label_rows_invalid_verdict_raises():
    rows = [{"output_id": "12", "trait_key": "k", "trait_class": "color", "human_verdict": "yes"}]
    with pytest.raises(ValueError, match="invalid human_verdict"):
        parse_label_rows(rows)


def test_parse_label_rows_normalizes_case_and_space():
    rows = [
        {
            "output_id": "12",
            "trait_key": "k",
            "trait_class": "color",
            "human_verdict": " Present_Correct ",
        }
    ]
    labels, _ = parse_label_rows(rows)
    assert labels == [(12, "k", "color", "present_correct")]


def test_vocab_matches_model():
    assert VOCAB == {"present_correct", "present_wrong", "absent", "not_assessable"}
