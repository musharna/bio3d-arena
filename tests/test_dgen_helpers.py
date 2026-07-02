# tests/test_dgen_helpers.py
from app.dgen import fidelity, build_critique, build_refine_prompt

TRAITS = [
    {"key": "leaf_form", "trait_class": "organ_shape", "expected": "trifoliate leaves"},
    {"key": "has_pod", "trait_class": "presence", "expected": "seed pods present"},
]


def test_fidelity_counts_present_correct_over_assessable():
    tr = [
        {"trait_key": "a", "verdict": "present_correct"},
        {"trait_key": "b", "verdict": "present_wrong"},
        {"trait_key": "c", "verdict": "absent"},
        {"trait_key": "d", "verdict": "not_assessable"},  # excluded
    ]
    fid, correct, assessable = fidelity(tr)
    assert correct == 1 and assessable == 3
    assert fid == 1 / 3


def test_fidelity_none_when_no_assessable():
    fid, correct, assessable = fidelity([{"trait_key": "a", "verdict": "not_assessable"}])
    assert fid is None and correct == 0 and assessable == 0


def test_build_critique_lists_failing_traits_organs_and_exec_error():
    tr = [
        {"trait_key": "leaf_form", "verdict": "absent"},
        {"trait_key": "has_pod", "verdict": "present_wrong"},
    ]
    crit = build_critique(
        tr, TRAITS, {"category": "partial-organism", "missing_organs": ["foliage"]}, "ok", ""
    )
    assert "leaf_form" in crit and "trifoliate leaves" in crit
    assert "has_pod" in crit
    assert "foliage" in crit and "partial-organism" in crit


def test_build_critique_includes_exec_error_when_run_failed():
    crit = build_critique(
        [],
        TRAITS,
        {"category": "complete", "missing_organs": []},
        "error",
        "Traceback: bpy.ops boom",
    )
    assert "bpy.ops boom" in crit
    assert "error" in crit.lower()


def test_build_critique_empty_when_nothing_to_fix():
    crit = build_critique(
        [{"trait_key": "leaf_form", "verdict": "present_correct"}],
        TRAITS,
        {"category": "complete", "missing_organs": []},
        "ok",
        "",
    )
    assert crit == ""


def test_build_refine_prompt_contains_base_prev_script_and_critique():
    p = build_refine_prompt("Glycine max", "soybean", "import bpy  # prev", "FIX leaf_form")
    assert "soybean" in p and "Glycine max" in p  # from base build_prompt
    assert "import bpy  # prev" in p  # previous script
    assert "FIX leaf_form" in p  # critique
    assert "ONLY" in p  # output-only instruction
