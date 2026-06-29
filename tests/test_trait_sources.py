from __future__ import annotations

from app import trait_sources


def test_wikidata_traits_maps_known_props_and_ignores_unmapped():
    def fake_sparql(_taxon):
        return {"qid": "Q23501", "props": {"P4000": "berry", "P2827": "yellow", "P9999": "x"}}

    out = trait_sources.wikidata_traits("Solanum lycopersicum", sparql_fn=fake_sparql)
    by = {t["key"]: t for t in out}
    assert by["wd_fruit_type"]["expected"] == "berry"
    assert by["wd_fruit_type"]["trait_class"] == "organ_shape"
    assert by["wd_fruit_type"]["citation"] == "https://www.wikidata.org/wiki/Q23501"
    assert by["wd_fruit_type"]["source_detail"] == "Q23501"
    assert by["wd_flower_color"]["trait_class"] == "color"
    assert len(out) == 2  # P9999 unmapped → ignored


def test_wikidata_traits_empty_when_no_item():
    assert trait_sources.wikidata_traits("Nonexistus", sparql_fn=lambda _t: None) == []
