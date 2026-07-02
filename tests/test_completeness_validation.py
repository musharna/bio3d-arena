# tests/test_completeness_validation.py
from app.completeness_validation import map_note_to_category, gt_by_output, agreement


def test_map_note_keywords():
    assert map_note_to_category("", "only a fruit, no plant") == "isolated-organ"
    assert map_note_to_category("", "partial plant, missing leaves") == "partial-organism"
    assert map_note_to_category("", "not a plant / junk") == "fragment"
    assert map_note_to_category("ok", "looks like a whole tomato plant") == "complete"
    assert map_note_to_category("", "") is None  # ambiguous -> drop


def test_gt_by_output_takes_worst_incompleteness_per_output():
    rows = [
        {"output_id": 1, "human_verdict": "", "note": "only a fruit"},
        {"output_id": 1, "human_verdict": "", "note": "looks fine"},
        {"output_id": 2, "human_verdict": "ok", "note": "whole plant"},
    ]
    gt = gt_by_output(rows)
    assert gt[1] == "isolated-organ"  # any incompleteness flag on an output wins
    assert gt[2] == "complete"


def test_agreement_computes_binary_kappa_and_isolated_recall():
    gt = {1: "isolated-organ", 2: "complete", 3: "complete", 4: "isolated-organ"}
    pred = {1: "isolated-organ", 2: "complete", 3: "complete", 4: "complete"}  # miss 4
    m = agreement(pred, gt)
    assert m["n"] == 4
    assert m["isolated_recall"] == 0.5  # caught 1 of 2 isolated
    assert -1.0 <= m["binary_kappa"] <= 1.0
