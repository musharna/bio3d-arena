from app.organ_inventory import Organ, inventory_for


def test_organ_complement_defaults_to_one():
    assert Organ("leg", "a leg", True).complement == 1  # existing callers unaffected


def test_dog_inventory_has_four_legs():
    inv = inventory_for("Canis lupus familiaris")
    assert inv is not None
    leg = next(o for o in inv.organs if o.key == "leg")
    assert leg.complement == 4 and leg.required is True


def test_animal_taxa_all_present():
    for t in (
        "Canis lupus familiaris",
        "Anas platyrhynchos",
        "Danaus plexippus",
        "Carassius auratus",
    ):
        assert inventory_for(t) is not None


def test_existing_plant_inventory_unchanged():
    inv = inventory_for("Solanum lycopersicum")
    assert all(o.complement == 1 for o in inv.organs)  # plants: every part singular
