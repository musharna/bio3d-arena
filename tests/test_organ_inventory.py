# tests/test_organ_inventory.py
from app.organ_inventory import ORGAN_INVENTORY, inventory_for

PLANT_TAXA = {
    "Solanum lycopersicum",
    "Zea mays",
    "Pinus sylvestris",
    "Rosa",
    "Glycine max",
    "Arabidopsis thaliana",
}
# Single-body-plan organisms: fungal fruiting bodies + a depicted fruit (see _body_inv).
BODY_PLAN_TAXA = {
    "Lycoperdon perlatum",
    "Cucurbita pepo",
    "Hericium erinaceus",
    "Boletus edulis",
    "Amanita muscaria",
    "Morchella esculenta",
    "Trametes versicolor",
}


def test_expected_taxa_present():
    assert set(ORGAN_INVENTORY) == PLANT_TAXA | BODY_PLAN_TAXA


def test_every_taxon_has_required_and_optional_organs_with_visuals():
    # Body-plan-agnostic invariants that must hold for every inventory.
    for taxon, inv in ORGAN_INVENTORY.items():
        req = {o.key for o in inv.organs if o.required}
        assert req, taxon  # at least one required organ
        # Most inventories keep the reproductive organ optional; Rosa is the deliberate
        # exception (flower-defining → all three organs required), so it has no optional organ.
        if taxon != "Rosa":
            assert any(not o.required for o in inv.organs), taxon
        assert all(o.visual.strip() for o in inv.organs), taxon  # visual descriptors (VLM read)


def test_plant_taxa_require_vegetative_axis_and_foliage():
    for taxon in PLANT_TAXA:
        req = {o.key for o in ORGAN_INVENTORY[taxon].organs if o.required}
        # Every plant requires the vegetative body (axis + foliage)...
        assert {"vegetative_axis", "foliage"} <= req, taxon
        # ...and only Rosa additionally requires its reproductive organ (the bloom).
        if taxon == "Rosa":
            assert req == {"vegetative_axis", "foliage", "reproductive_flower_hip"}
        else:
            assert req == {"vegetative_axis", "foliage"}, taxon


def test_body_plan_taxa_have_single_required_body_organ():
    # The fruiting/fruit body is the SOLE required organ; features are optional.
    for taxon in BODY_PLAN_TAXA:
        req = [o.key for o in ORGAN_INVENTORY[taxon].organs if o.required]
        assert len(req) == 1, taxon


def test_inventory_for_unknown_taxon_is_none():
    assert inventory_for("Homo sapiens") is None
    assert inventory_for("Zea mays").taxon == "Zea mays"
