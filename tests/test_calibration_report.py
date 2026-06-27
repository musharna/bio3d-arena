from __future__ import annotations

import math

from app import calibration


def test_cohens_kappa_perfect_agreement_is_one():
    a = ["first", "second", "tie", "first"]
    assert math.isclose(calibration.cohens_kappa(a, list(a)), 1.0)


def test_cohens_kappa_chance_agreement_near_zero():
    # Independent 50/50 labels → κ near 0 (allow slack on a tiny sample).
    a = ["first", "second"] * 10
    b = ["first", "first", "second", "second"] * 5
    k = calibration.cohens_kappa(a, b)
    assert -0.5 < k < 0.5


def test_canonical_label_is_order_independent():
    # out_a=10 (lower) wins → 'first'; swap slots, out_a=20 wins via 'b' → still 'first'.
    assert calibration.canonical_label("a", 10, 20) == "first"
    assert calibration.canonical_label("b", 20, 10) == "first"
    assert calibration.canonical_label("tie", 10, 20) == "tie"


def test_cohens_kappa_handles_empty():
    assert calibration.cohens_kappa([], []) is None
