# tests/test_completeness_validation.py
from app.completeness_validation import map_note_to_category, gt_by_output, agreement


def test_map_note_keywords():
    # incompleteness categories come from the human note; complete does NOT (it comes from a
    # present_correct verdict in gt_by_output), so a "whole plant" note alone maps to None.
    assert map_note_to_category("", "only a fruit, no plant") == "isolated-organ"
    assert map_note_to_category("", "leafless/fruitless/just sticks") == "partial-organism"
    assert map_note_to_category("", "not a plant / junk") == "fragment"
    assert map_note_to_category("present_correct", "looks like a whole tomato plant") is None
    assert map_note_to_category("", "") is None  # ambiguous -> drop


def test_specific_bucket_beats_generic_junk():
    # the core bug this fixes: "junk --fruit" is an isolated fruit, NOT a fragment.
    assert map_note_to_category("", "not a plant / junk --fruit") == "isolated-organ"
    assert map_note_to_category("", "not a plant / junk/ fruit") == "isolated-organ"
    # a bare junk note (no organ qualifier) stays fragment.
    assert map_note_to_category("", "not a plant / junk") == "fragment"


def test_trait_quibbles_drop():
    for note in ("bad trait", "weird trait", "no color", "too low-res to judge", "no flowers"):
        assert map_note_to_category("absent", note) is None


def test_gt_by_output_worst_incompleteness_and_positive_complete():
    rows = [
        # output 1: an isolated-fruit note wins over a benign trait row
        {"output_id": 1, "human_verdict": "absent", "note": "only a fruit"},
        {"output_id": 1, "human_verdict": "present_correct", "note": ""},
        # output 2: no incompleteness note + a present_correct verdict -> complete
        {"output_id": 2, "human_verdict": "present_correct", "note": "good model. bad trait"},
        # output 3: worst-severity across two incompleteness notes (fragment < partial)
        {"output_id": 3, "human_verdict": "absent", "note": "stub -- not a full plant"},
        {"output_id": 3, "human_verdict": "absent", "note": "not a plant / junk"},
        # output 4: only a trait-quibble, no present_correct -> dropped
        {"output_id": 4, "human_verdict": "absent", "note": "bad trait"},
    ]
    gt = gt_by_output(rows)
    assert gt[1] == "isolated-organ"  # incompleteness outranks the present_correct trait
    assert gt[2] == "complete"  # positive signal from present_correct
    assert gt[3] == "fragment"  # worst severity wins
    assert 4 not in gt  # dropped


def test_agreement_computes_binary_kappa_and_isolated_recall():
    gt = {1: "isolated-organ", 2: "complete", 3: "complete", 4: "isolated-organ"}
    pred = {1: "isolated-organ", 2: "complete", 3: "complete", 4: "complete"}  # miss 4
    m = agreement(pred, gt)
    assert m["n"] == 4
    assert m["isolated_recall"] == 0.5  # caught 1 of 2 isolated
    assert -1.0 <= m["binary_kappa"] <= 1.0
