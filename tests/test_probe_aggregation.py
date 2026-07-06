"""Pure aggregation for the CLIP/BioCLIP feasibility probe (scripts/probe_clip_bioclip.py).

Only `confusion()` is unit-tested here — it is deterministic (no torch/open_clip/Anthropic
call). The GPU/API probe run (`main()`) is a later controller step."""

from scripts.probe_clip_bioclip import confusion


def test_confusion_counts_tp_fp():
    recs = [
        {"true": "fruit_only", "pred_clip": "fruit_only"},  # TP
        {"true": "good", "pred_clip": "fruit_only"},  # FP
        {"true": "good", "pred_clip": "good"},  # TN
        {"true": "fruit_only", "pred_clip": "good"},  # FN
    ]
    c = confusion(recs)["clip"]["fruit_only"]
    assert (c["tp"], c["fp"], c["tn"], c["fn"]) == (1, 1, 1, 1)


def test_confusion_is_per_mechanism_and_skips_missing_predictions():
    # bioclip only has a prediction on 2 of the 3 records (mirrors photo-vs-render domain
    # items only carrying pred_<mech> for the mechanisms applicable to their domain).
    recs = [
        {
            "true": "wrong_species",
            "pred_clip": "good",
            "pred_bioclip": "wrong_species",
        },  # TP for bioclip
        {"true": "good", "pred_clip": "good", "pred_bioclip": "good"},  # TN for bioclip
        {
            "true": "wrong_species",
            "pred_clip": "good",
        },  # no bioclip pred -> excluded from bioclip counts
    ]
    out = confusion(recs)
    assert set(out) == {"clip", "bioclip"}
    # clip never predicts "wrong_species" -> both wrong_species records are FN, the good record is TN
    assert out["clip"]["wrong_species"] == {"tp": 0, "fp": 0, "tn": 1, "fn": 2}
    # bioclip only saw 2 of the 3 records (one had no pred_bioclip key)
    assert out["bioclip"]["wrong_species"] == {"tp": 1, "fp": 0, "tn": 1, "fn": 0}


def test_confusion_multiple_defects_are_independent_one_vs_rest_columns():
    recs = [
        {"true": "fruit_only", "pred_vlm": "fruit_only"},
        {"true": "poor_exemplar", "pred_vlm": "poor_exemplar"},
        {"true": "good", "pred_vlm": "good"},
    ]
    out = confusion(recs)["vlm"]
    assert set(out) == {"fruit_only", "poor_exemplar"}
    assert out["fruit_only"] == {"tp": 1, "fp": 0, "tn": 2, "fn": 0}
    assert out["poor_exemplar"] == {"tp": 1, "fp": 0, "tn": 2, "fn": 0}
