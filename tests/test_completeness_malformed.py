# tests/test_completeness_malformed.py
from app.completeness import derive
from app.organ_inventory import inventory_for


def _present(inv, complement_overrides=None):
    ov = complement_overrides or {}
    out = []
    for o in inv.organs:
        item = {"key": o.key, "status": "present"}
        if o.complement > 1:
            item["complement"] = ov.get(o.key, "full")
        out.append(item)
    return out


def test_all_present_full_complement_is_complete():
    inv = inventory_for("Canis lupus familiaris")
    cat, score = derive(inv, _present(inv))
    assert cat == "complete" and score == 1.0


def test_missing_leg_is_malformed_not_complete():
    inv = inventory_for("Canis lupus familiaris")
    cat, score = derive(inv, _present(inv, {"leg": "missing_some"}))  # 3-legged dog
    assert cat == "malformed"
    assert score == 1.0  # all part-types present -> coverage 1.0; category carries the signal


def test_missing_whole_part_type_is_partial_not_malformed():
    inv = inventory_for("Canis lupus familiaris")
    present = [
        {
            "key": o.key,
            "status": ("absent" if o.key == "head" else "present"),
            **({"complement": "full"} if o.complement > 1 else {}),
        }
        for o in inv.organs
    ]
    cat, _ = derive(inv, present)
    assert cat == "partial-organism"  # a whole part-type absent, not malformed


def test_plant_inventory_never_malformed():
    inv = inventory_for("Solanum lycopersicum")
    present = [{"key": o.key, "status": "present"} for o in inv.organs]
    cat, _ = derive(inv, present)
    assert cat == "complete"  # plants: all complement 1 -> complements trivially full
