"""The reference galleries were sourced against whatever iNaturalist's fuzzy text search
returned first, with no check on what came back. Measured over all 17 taxa on 2026-07-29:
`q=Rosa` resolved to the ORDER **Rosales** — brambles, elms, figs, nettles — and *Hericium
erinaceus*, *Cucurbita pepo* and *Trametes versicolor* each resolved to rank `complex`, which
is by definition a group of species too similar to tell apart. Between them those four
accounted for 15 of 47 wrong reference photos, including lion's mane coming back 8/8 as coral
tooth and bear's head.

The resolver now requires an exact name match at a rank narrow enough for the photo pool to
depict a single organism. These tests use a stub for the HTTP call; the live-API check that
all 17 arena taxa now resolve to species/genus is in the commit message.
"""

from __future__ import annotations

import pytest

from scripts import source_reference_gallery as srg


def _stub(monkeypatch, results):
    monkeypatch.setattr(srg, "_get", lambda url: {"results": results})


def test_an_order_is_rejected_even_when_it_is_the_top_hit(monkeypatch):
    """THE rose bug. Rosales ranked first for `q=Rosa`, so `results[0]` took it, and the gallery
    filled with anything in the order — which is why voters saw brambles."""
    _stub(monkeypatch, [{"id": 47148, "name": "Rosales", "rank": "order"}])
    assert srg._resolve_taxon_id("Rosa") is None


def test_the_right_name_deeper_in_the_list_is_preferred_over_a_wrong_top_hit(monkeypatch):
    _stub(
        monkeypatch,
        [
            {"id": 47148, "name": "Rosales", "rank": "order"},
            {"id": 47216, "name": "Rosa", "rank": "genus"},
        ],
    )
    assert srg._resolve_taxon_id("Rosa") == 47216


@pytest.mark.parametrize("rank", ["complex", "order", "family", "class", "kingdom"])
def test_ranks_whose_photos_span_multiple_organisms_are_rejected(monkeypatch, rank):
    """A `complex` carries the right NAME, so a name-only check would admit it — and its curated
    photos legitimately include sibling species. Rank has to be checked too."""
    _stub(monkeypatch, [{"id": 1, "name": "Hericium erinaceus", "rank": rank}])
    assert srg._resolve_taxon_id("Hericium erinaceus") is None


@pytest.mark.parametrize("rank", ["species", "subspecies", "variety", "genus"])
def test_ranks_that_depict_one_organism_are_accepted(monkeypatch, rank):
    _stub(monkeypatch, [{"id": 7, "name": "Hericium erinaceus", "rank": rank}])
    assert srg._resolve_taxon_id("Hericium erinaceus") == 7


def test_a_near_miss_name_is_not_accepted(monkeypatch):
    """Positive control on the name match: the sibling species is a species and looks alike, so
    only the name distinguishes it. If this passed, the matcher would be inert."""
    _stub(monkeypatch, [{"id": 2, "name": "Hericium coralloides", "rank": "species"}])
    assert srg._resolve_taxon_id("Hericium erinaceus") is None


def test_the_arena_name_is_translated_to_the_inaturalist_name(monkeypatch):
    """iNaturalist calls the domestic dog *Canis familiaris*; the arena calls it *Canis lupus
    familiaris* and that name is load-bearing (organ inventory key, gallery slug, task title).
    Translating the query keeps the strict match without renaming the corpus."""
    seen = {}

    def _get(url):
        seen["url"] = url
        return {"results": [{"id": 47144, "name": "Canis familiaris", "rank": "species"}]}

    monkeypatch.setattr(srg, "_get", _get)
    assert srg._resolve_taxon_id("Canis lupus familiaris") == 47144
    assert "Canis+familiaris" in seen["url"]


def test_no_match_resolves_to_none_rather_than_a_wrong_guess(monkeypatch):
    _stub(monkeypatch, [])
    assert srg._resolve_taxon_id("Nothing here") is None


# --- cultivated taxa ---------------------------------------------------------------------
#
# Measured on the live API, 2026-07-29. iNaturalist's `quality_grade=research` requires an
# observation to be WILD, so for a crop or ornamental it selects against exactly the form the
# arena's task asks generators to produce:
#
#   Solanum lycopersicum  20,742 wild / 4,410 cultivated (CC-licensed)
#   Rosa (genus)           2,446 wild /     1 cultivated
#
# The tomato gallery came back as feral volunteers in urban stormwater drains — every one a
# valid wild record, none of them the cultivated plant the task depicts. Those 4,410 cultivated
# observations were never reachable. (Rosa is NOT fixable this way: iNaturalist simply has no
# CC-licensed cultivated roses. Recorded here so the null is not re-derived.)


def test_a_cultivated_taxon_asks_for_cultivated_observations(monkeypatch):
    seen = {}

    def _get(url):
        seen["url"] = url
        return {"results": []}

    monkeypatch.setattr(srg, "_get", _get)
    srg._cc_photos_from_observations(50623, 8, cultivated=True)
    assert "captive=true" in seen["url"]
    assert "quality_grade=research" not in seen["url"], (
        "research grade REQUIRES wild — combining it with captive returns nothing"
    )


def test_a_wild_taxon_still_asks_for_research_grade(monkeypatch):
    """Regression guard: the wild path must not change. Most arena taxa are genuinely wild
    organisms where research-grade is exactly the right filter."""
    seen = {}

    def _get(url):
        seen["url"] = url
        return {"results": []}

    monkeypatch.setattr(srg, "_get", _get)
    srg._cc_photos_from_observations(55779, 8)
    assert "quality_grade=research" in seen["url"]
    assert "captive=true" not in seen["url"]


def test_the_domesticated_roster_covers_crops_and_pets():
    """These are the taxa whose tasks depict a cultivated plant. Rosa is deliberately absent:
    the cultivated pool does not exist for it, so routing it here would silently return nothing
    instead of failing visibly."""
    assert "Solanum lycopersicum" in srg.DOMESTICATED
    assert "Rosa" not in srg.DOMESTICATED
    assert "Canis lupus familiaris" in srg.DOMESTICATED
    assert "Danaus plexippus" not in srg.DOMESTICATED
