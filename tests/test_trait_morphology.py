from app.traits import is_visually_judgeable
from app.trait_morphology import (
    MORPHOLOGY_TRAITS,
    WIKISPECIES_URL,
    build_morphology_rubric,
)

TAXA = [
    "Solanum lycopersicum",
    "Zea mays",
    "Pinus sylvestris",
    "Rosa",
    "Glycine max",
    "Arabidopsis thaliana",
]


def test_all_six_taxa_present_with_enough_traits():
    assert set(MORPHOLOGY_TRAITS) == set(TAXA)
    for taxon, traits in MORPHOLOGY_TRAITS.items():
        assert 8 <= len(traits) <= 12, f"{taxon} has {len(traits)} traits"


def test_every_authored_trait_is_judgeable_and_scoreable():
    from app.traits import SCORED_CLASSES

    for taxon, traits in MORPHOLOGY_TRAITS.items():
        for t in traits:
            t2 = {**t, "taxon": taxon}
            assert is_visually_judgeable(t2), f"{taxon}/{t['key']} not judgeable"
            assert t["trait_class"] in SCORED_CLASSES


def test_pine_has_no_flower_or_fruit_traits():
    keys = " ".join(t["key"] for t in MORPHOLOGY_TRAITS["Pinus sylvestris"]).lower()
    assert "flower" not in keys and "fruit" not in keys


def test_wikispecies_url():
    assert (
        WIKISPECIES_URL("Solanum lycopersicum")
        == "https://species.wikimedia.org/wiki/Solanum_lycopersicum"
    )
    assert WIKISPECIES_URL("Rosa") == "https://species.wikimedia.org/wiki/Rosa"


def test_build_merges_db_tier_and_stamps_citations():
    # stub Wikidata: one fruit-type (P4000) value for tomato
    def fake_sparql(taxon):
        return {"qid": "Q23501", "props": {"P4000": "berry"}}

    traits = build_morphology_rubric("Solanum lycopersicum", sparql_fn=fake_sparql)
    assert any(t["source_tier"] == "db" for t in traits)  # Wikidata contributed
    assert all((t.get("citation") or "").strip() for t in traits)  # everything cited
    assert all(t["source_tier"] in ("db", "ref") for t in traits)
    # ref-tier traits cite Wikispecies
    refs = [t for t in traits if t["source_tier"] == "ref"]
    assert all(
        t["citation"] == "https://species.wikimedia.org/wiki/Solanum_lycopersicum" for t in refs
    )


def test_build_dedups_db_over_ref():
    # authored ref has an organ_shape fruit; Wikidata also returns a fruit type → one wins, no dup
    def fake_sparql(taxon):
        return {"qid": "Q23501", "props": {"P4000": "berry"}}

    traits = build_morphology_rubric("Solanum lycopersicum", sparql_fn=fake_sparql)
    keys = [(t["trait_class"], t["expected"].lower()) for t in traits]
    assert len(keys) == len(set(keys)), "duplicate (trait_class, expected)"


def test_build_no_wikidata_still_valid():
    traits = build_morphology_rubric("Pinus sylvestris", sparql_fn=lambda taxon: None)
    assert len(traits) >= 8
    assert all(t["source_tier"] == "ref" for t in traits)
