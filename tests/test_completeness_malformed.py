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


def test_missing_leg_complement_is_complete_not_malformed():
    # A limb-count complement (e.g. VLM reports 3 of 4 legs) is ADVISORY, not category-gating:
    # the VLM cannot reliably count thin paired limbs from a turntable sheet (a correctly
    # 4-legged dog is routinely miscounted), so derive() no longer promotes that noise to a
    # `malformed` category. Every required part-TYPE is present -> complete.
    inv = inventory_for("Canis lupus familiaris")
    cat, score = derive(inv, _present(inv, {"leg": "missing_some"}))  # VLM "3-legged" dog
    assert cat == "complete"
    assert score == 1.0  # all part-types present -> coverage 1.0


def test_derive_never_returns_malformed_for_any_complement_state():
    # No complement configuration (missing_some / extra / uncertain, on any paired organ)
    # yields `malformed` — the noisy category is gone for every animal inventory.
    for taxon in (
        "Canis lupus familiaris",
        "Anas platyrhynchos",
        "Danaus plexippus",
        "Carassius auratus",
    ):
        inv = inventory_for(taxon)
        paired = [o.key for o in inv.organs if o.complement > 1]
        for state in ("missing_some", "extra", "uncertain"):
            overrides = {k: state for k in paired}
            cat, _ = derive(inv, _present(inv, overrides))
            assert cat != "malformed", f"{taxon} {state} still malformed"
            assert cat == "complete", f"{taxon} {state} -> {cat}, expected complete"


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
