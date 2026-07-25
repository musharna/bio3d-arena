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
# Single-body-plan organisms — fungal fruiting bodies ONLY (see _body_inv). A fungus's whole
# macro-organism IS its fruiting body; a PLANT's organism is the whole plant, so no plant taxon
# may be single-body. Cucurbita pepo (a pumpkin fruit = one organ of the vine) was wrongly listed
# here and removed — a plant fruit is not a complete organism.
FUNGI_TAXA = {
    "Lycoperdon perlatum",
    "Hericium erinaceus",
    "Boletus edulis",
    "Amanita muscaria",
    "Morchella esculenta",
    "Trametes versicolor",
}
# Bilaterian animal body plans (see _animal_inv): several required parts, each with an expected
# complement. Every part is required, so — like Rosa — an animal inventory has no optional organ.
ANIMAL_TAXA = {
    "Canis lupus familiaris",
    "Anas platyrhynchos",
    "Danaus plexippus",
    "Carassius auratus",
}
# Inventories whose body plan is wholly required, so the "has an optional organ" invariant below
# does not apply: Rosa (flower-defining) plus every animal (all body parts required).
ALL_REQUIRED_TAXA = {"Rosa"} | ANIMAL_TAXA


def test_expected_taxa_present():
    assert set(ORGAN_INVENTORY) == PLANT_TAXA | FUNGI_TAXA | ANIMAL_TAXA


def test_every_taxon_has_required_and_optional_organs_with_visuals():
    # Body-plan-agnostic invariants that must hold for every inventory.
    for taxon, inv in ORGAN_INVENTORY.items():
        req = {o.key for o in inv.organs if o.required}
        assert req, taxon  # at least one required organ
        # Most inventories keep the reproductive organ optional; the exceptions are Rosa
        # (flower-defining → all three organs required) and the animals (every body part
        # required), which by design have no optional organ.
        if taxon not in ALL_REQUIRED_TAXA:
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


def test_fungi_have_single_required_body_organ():
    # The fruiting body is the SOLE required organ; features are optional.
    for taxon in FUNGI_TAXA:
        req = [o.key for o in ORGAN_INVENTORY[taxon].organs if o.required]
        assert len(req) == 1, taxon


def test_single_body_scope_is_fungi_only():
    """A plant's organism is the whole plant (axis + foliage), so a plant fruit must never be
    scored 'complete' as a lone body — only a fungus (its fruiting body) is a single-required-body
    organism. Guards the Cucurbita-pepo-as-fruit regression: a pumpkin fruit is one organ of the
    vine, not a complete organism, and must not re-enter the inventory as a body plan."""
    single_body = {
        t for t, inv in ORGAN_INVENTORY.items() if sum(o.required for o in inv.organs) == 1
    }
    assert single_body == FUNGI_TAXA, (
        f"non-fungi with single-body scope: {single_body - FUNGI_TAXA}"
    )
    assert inventory_for("Cucurbita pepo") is None  # plant-fruit-as-body entry removed


def test_inventory_for_unknown_taxon_is_none():
    assert inventory_for("Homo sapiens") is None
    assert inventory_for("Zea mays").taxon == "Zea mays"
