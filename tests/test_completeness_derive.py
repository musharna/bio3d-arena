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


FUNGAL = inventory_for("Lycoperdon perlatum")  # required: fruiting_body (only); optional x2


def _pf(*present_keys):
    keys = ["fruiting_body", "sterile_base", "surface_ornament"]
    return [{"key": k, "status": "present" if k in present_keys else "absent"} for k in keys]


def test_fungal_lone_body_is_complete_not_isolated():
    # A single-required-organ body plan: the fruiting body IS the whole organism, so a render
    # showing only it must be 'complete' (score 1.0), NOT 'isolated-organ'. This is the case
    # the derive() reorder fixes; the plant tests above prove the reorder left plants unchanged.
    assert derive(FUNGAL, _pf("fruiting_body")) == ("complete", 1.0)
    assert derive(FUNGAL, _pf("fruiting_body", "surface_ornament")) == ("complete", 1.0)


def test_fungal_body_absent_is_incomplete():
    assert derive(FUNGAL, _pf()) == ("fragment", 0.0)
    # only an optional feature, no body -> one organ present but not the required one
    assert derive(FUNGAL, _pf("surface_ornament")) == ("isolated-organ", 0.0)


def test_fungal_inventory_has_single_required_body():
    req = [o for o in FUNGAL.organs if o.required]
    assert [o.key for o in req] == ["fruiting_body"]
    assert all(inventory_for(t) is not None for t in ("Cucurbita pepo", "Hericium erinaceus"))


def test_extraneous_present_key_does_not_inflate_count():
    # VLM hallucinates an extra organ not in the inventory, marked present.
    organs = [
        {"key": "vegetative_axis", "status": "present"},
        {"key": "foliage", "status": "absent"},
        {"key": "reproductive_fruit", "status": "absent"},
        {"key": "reproductive_bogus", "status": "present"},  # not in the tomato inventory
    ]
    cat, score = derive(INV, organs)
    assert cat == "isolated-organ"  # only 1 real inventory organ present, not 2
    assert score == 0.5
