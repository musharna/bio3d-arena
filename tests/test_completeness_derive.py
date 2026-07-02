# tests/test_completeness_derive.py
from app.completeness import derive
from app.organ_inventory import inventory_for

INV = inventory_for(
    "Solanum lycopersicum"
)  # required: vegetative_axis, foliage; optional: reproductive_fruit


def _p(*present_keys):
    # build organs_present marking listed keys present, the rest absent
    keys = ["vegetative_axis", "foliage", "reproductive_fruit"]
    return [{"key": k, "status": "present" if k in present_keys else "absent"} for k in keys]


def test_complete_when_all_required_present():
    assert derive(INV, _p("vegetative_axis", "foliage")) == ("complete", 1.0)
    assert derive(INV, _p("vegetative_axis", "foliage", "reproductive_fruit")) == ("complete", 1.0)


def test_partial_organism_when_two_present_but_a_required_absent():
    # foliage + fruit present, axis absent -> present_count 2, required axis missing
    cat, score = derive(INV, _p("foliage", "reproductive_fruit"))
    assert cat == "partial-organism"
    assert score == 0.5


def test_isolated_organ_when_exactly_one_present():
    assert derive(INV, _p("reproductive_fruit")) == ("isolated-organ", 0.0)  # lone fruit
    assert derive(INV, _p("vegetative_axis")) == ("isolated-organ", 0.5)  # lone stem


def test_fragment_when_none_present():
    assert derive(INV, _p()) == ("fragment", 0.0)


def test_uncertain_never_upgrades():
    organs = [
        {"key": "vegetative_axis", "status": "present"},
        {"key": "foliage", "status": "uncertain"},
        {"key": "reproductive_fruit", "status": "absent"},
    ]
    cat, score = derive(
        INV, organs
    )  # only 1 present -> isolated-organ, uncertain foliage not counted
    assert cat == "isolated-organ"
    assert score == 0.5
