import pytest
from scripts.author_morphology_rubrics import assemble_all, verify_resolves


def test_assemble_all_covers_six_taxa():
    res = assemble_all(sparql_fn=lambda taxon: None)
    assert len(res) == 6
    for taxon, traits in res.items():
        assert len(traits) >= 8
        assert all(t["source_tier"] in ("db", "ref") for t in traits)


def test_verify_resolves_passes_when_all_ok():
    traits = [{"key": "a", "citation": "https://x"}, {"key": "b", "citation": "https://y"}]
    verify_resolves(traits, resolve_fn=lambda url: True)  # no raise


def test_verify_resolves_fails_loud_on_dead_link():
    traits = [{"key": "a", "citation": "https://x"}, {"key": "b", "citation": "https://dead"}]
    with pytest.raises(ValueError, match="did not resolve"):
        verify_resolves(traits, resolve_fn=lambda url: url != "https://dead")
