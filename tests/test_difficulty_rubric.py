# tests/test_difficulty_rubric.py
import pytest

from app import difficulty_rubric as dr


def test_tier_thresholds_at_boundaries():
    base = {a: 0 for a in dr.AXES}
    assert dr.tier_for_scores({**base}) == "easy"  # sum 0
    assert dr.tier_for_scores({**base, "fine_detail": 2, "self_occlusion": 1}) == "easy"  # sum 3
    assert (
        dr.tier_for_scores({**base, "fine_detail": 2, "self_occlusion": 2}) == "moderate"
    )  # sum 4
    assert (
        dr.tier_for_scores({**base, "fine_detail": 2, "self_occlusion": 2, "non_rigidity": 2})
        == "moderate"
    )  # sum 6
    assert (
        dr.tier_for_scores(
            {
                "fine_detail": 2,
                "self_occlusion": 2,
                "non_rigidity": 2,
                "topology": 1,
                "thin_structure": 0,
            }
        )
        == "hard"
    )  # sum 7
    assert dr.tier_for_scores({a: 2 for a in dr.AXES}) == "hard"  # sum 10


def test_tier_for_scores_fails_loud():
    good = {a: 1 for a in dr.AXES}
    with pytest.raises(ValueError):
        dr.tier_for_scores({a: 1 for a in dr.AXES[:-1]})  # missing axis
    with pytest.raises(ValueError):
        dr.tier_for_scores({**good, "bogus": 1})  # unknown key
    with pytest.raises(ValueError):
        dr.tier_for_scores({**good, "topology": 3})  # out of range
    with pytest.raises(ValueError):
        dr.tier_for_scores({**good, "topology": -1})


def test_rubric_complete_and_scored():
    expected = {
        "solanum_lycopersicum": "easy",
        "zea_mays": "moderate",
        "glycine_max": "moderate",
        "arabidopsis_thaliana": "hard",
        "pinus_sylvestris": "hard",
        "rosa": "hard",
        "hordeum_vulgare": "hard",
        # Kingdom Fungi + easy-plant expansion
        "cucurbita_pepo": "easy",
        "lycoperdon_perlatum": "easy",
        "hericium_erinaceus": "hard",
    }
    assert set(dr.RUBRIC) == set(expected)
    for slug, entry in dr.RUBRIC.items():
        assert set(entry["scores"]) == set(dr.AXES), slug
        assert set(entry["rationale"]) == set(dr.AXES), slug
        assert all(isinstance(v, int) and 0 <= v <= 2 for v in entry["scores"].values()), slug
        assert dr.taxon_tier(slug) == expected[slug]


def test_taxon_lookups_fail_loud():
    with pytest.raises(ValueError):
        dr.taxon_tier("unknown_species")
    with pytest.raises(ValueError):
        dr.taxon_axes("unknown_species")
