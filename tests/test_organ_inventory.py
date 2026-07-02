# tests/test_organ_inventory.py
from app.organ_inventory import ORGAN_INVENTORY, inventory_for


def test_all_six_taxa_present():
    assert set(ORGAN_INVENTORY) == {
        "Solanum lycopersicum",
        "Zea mays",
        "Pinus sylvestris",
        "Rosa",
        "Glycine max",
        "Arabidopsis thaliana",
    }


def test_every_taxon_has_required_vegetative_body_and_optional_reproductive():
    for taxon, inv in ORGAN_INVENTORY.items():
        keys = {o.key for o in inv.organs}
        assert {"vegetative_axis", "foliage"} <= keys, taxon
        # vegetative body is required; at least one reproductive organ, and it is optional
        req = {o.key for o in inv.organs if o.required}
        assert req == {"vegetative_axis", "foliage"}, taxon
        assert any(not o.required for o in inv.organs), taxon
        # every organ has a non-empty visual descriptor (a completeness read must be visual)
        assert all(o.visual.strip() for o in inv.organs), taxon


def test_inventory_for_unknown_taxon_is_none():
    assert inventory_for("Homo sapiens") is None
    assert inventory_for("Zea mays").taxon == "Zea mays"
