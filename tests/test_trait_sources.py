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


class _ExtractClient:
    """Anthropic-like stub returning a forced record_extracted_traits tool_use."""

    class _Block:
        type = "tool_use"
        name = "record_extracted_traits"

        def __init__(self, data):
            self.input = data

    class _Resp:
        def __init__(self, data):
            self.content = [_ExtractClient._Block(data)]

    def __init__(self, payload):
        self._payload = payload
        self.messages = self

    def create(self, **_kw):
        return _ExtractClient._Resp({"traits": self._payload})


def test_literature_grounded_keeps_quoted_drops_unquoted_and_bad_class():
    text = "The corolla is bright red. Leaves are compound."
    payload = [
        {
            "key": "petal_color",
            "trait_class": "color",
            "expected": "red",
            "quote": "The corolla is bright red",
        },  # quote ⊂ text → kept
        {
            "key": "leaf_shape",
            "trait_class": "organ_shape",
            "expected": "compound",
            "quote": "Leaves are palmate",
        },  # quote NOT in text → dropped
        {
            "key": "height",
            "trait_class": "height",
            "expected": "2m",
            "quote": "Leaves are compound",
        },  # bad class → dropped
    ]
    out = trait_sources.literature_grounded_traits(
        "Testus planta",
        search_fn=lambda _t: [{"doi": "10.1/x", "title": "T"}],
        resolve_fn=lambda _p: text,
        llm_client=_ExtractClient(payload),
    )
    assert len(out) == 1
    t = out[0]
    assert t["key"] == "petal_color" and t["trait_class"] == "color"
    assert "source_tier" not in t  # source_tier is stamped later by build_rubric_traits
    assert t["citation"] == "10.1/x" and t["quote"] == "The corolla is bright red"


def test_literature_grounded_skips_pubs_with_no_text():
    out = trait_sources.literature_grounded_traits(
        "Testus",
        search_fn=lambda _t: [{"doi": "10.1/x"}],
        resolve_fn=lambda _p: None,  # unresolvable → skipped, no LLM call
        llm_client=_ExtractClient([]),
    )
    assert out == []


def _trait(citation):
    return {
        "key": "k",
        "trait_class": "color",
        "type": "categorical",
        "expected": "red",
        "visual": True,
        "citation": citation,
        "source_detail": citation,
        "quote": "q",
    }


def test_verify_citations_gates_papers_and_wikidata():
    traits = [
        _trait("10.1/real"),  # paper, verified → kept
        _trait("10.1/fake"),  # paper, unverified → dropped
        _trait("10.1/retracted"),  # paper, retracted → dropped
        _trait("https://www.wikidata.org/wiki/Q23501"),  # wikidata, resolvable → kept
        _trait("https://www.wikidata.org/wiki/Q0"),  # wikidata, unresolvable → dropped
    ]
    gc = {
        "10.1/real": {"verified": True, "retracted": False},
        "10.1/fake": {"verified": False, "retracted": False},
        "10.1/retracted": {"verified": True, "retracted": True},
    }
    kept = trait_sources.verify_citations(
        traits,
        ghostcite_fn=lambda c: gc.get(c, {"verified": False, "retracted": False}),
        resolve_fn=lambda url: url.endswith("Q23501"),
    )
    cites = [t["citation"] for t in kept]
    assert cites == ["10.1/real", "https://www.wikidata.org/wiki/Q23501"]
