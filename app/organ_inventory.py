# app/organ_inventory.py
"""Authored per-taxon expected-organ inventories for the organism-level completeness metric.

Required organs = the vegetative body (a plant is "complete" if it has an axis + foliage).
The reproductive organ is OPTIONAL so a lone fruit/cone/pod registers as an isolated organ,
not a complete plant. Visual descriptors are image-judgeable phrases for the VLM checklist.
Taxon keys MUST match app.trait_morphology.MORPHOLOGY_TRAITS (== TraitRubric.taxon).

FOLIAGE descriptors ask leaf PRESENCE, not leaf morphology: an earlier version baked in leaf
shape ("trifoliate", "strap-like", "pinnate serrated"), which made the VLM hedge "uncertain" on
obviously-present leaves it couldn't confirm the exact shape of, demoting complete plants. AXIS
and reproductive descriptors stay specific — loosening the axis let a lone fruit's pedicel count
as a stem, wrecking isolated-organ detection (leaf-shape is the trait rubric's job, not ours)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Organ:
    key: str
    visual: str
    required: bool


@dataclass(frozen=True)
class TaxonInventory:
    taxon: str
    organs: tuple[Organ, ...]


def _inv(taxon: str, axis: str, foliage: str, repro_key: str, repro: str) -> TaxonInventory:
    return TaxonInventory(
        taxon=taxon,
        organs=(
            Organ("vegetative_axis", axis, True),
            Organ("foliage", foliage, True),
            Organ(repro_key, repro, False),
        ),
    )


def _body_inv(taxon: str, body_key: str, body: str, *optional: tuple[str, str]) -> TaxonInventory:
    """Body-plan inventory for organisms whose whole body is a single unit — fungal fruiting
    bodies and a depicted fruit (the arena's Cucurbita reference shows the pumpkin fruit, not
    the vine). The body is the SOLE required organ (approved 'body required, features optional'
    completeness model, 2026-07-04); diagnostic surface features and basal attachment are
    optional, so their absence in a render makes an output 'partial', not incomplete. This is an
    UNCALIBRATED cross-kingdom extension of the plant-calibrated (κ=0.64) completeness metric."""
    organs = [Organ(body_key, body, True)]
    organs += [Organ(k, v, False) for k, v in optional]
    return TaxonInventory(taxon=taxon, organs=tuple(organs))


ORGAN_INVENTORY: dict[str, TaxonInventory] = {
    "Solanum lycopersicum": _inv(
        "Solanum lycopersicum",
        "an upright central green stem",
        "green leaves along the stem",
        "reproductive_fruit",
        "round red/green fleshy berries",
    ),
    "Zea mays": _inv(
        "Zea mays",
        "a tall single vertical stalk",
        "leaf blades along the stalk",
        "reproductive_inflorescence",
        "a terminal tassel and/or a lateral ear",
    ),
    "Pinus sylvestris": _inv(
        "Pinus sylvestris",
        "a woody trunk with branches",
        "needles on the branches",
        "reproductive_cone",
        "egg/cone-shaped woody cones",
    ),
    "Rosa": _inv(
        "Rosa",
        "a thorny woody stem",
        "green leaves on the stem",
        "reproductive_flower_hip",
        "a flower and/or a rounded fleshy rose hip",
    ),
    "Glycine max": _inv(
        "Glycine max",
        "an erect branching stem",
        "green leaves on the stem",
        "reproductive_pod",
        "narrow fuzzy seed pods",
    ),
    "Arabidopsis thaliana": _inv(
        "Arabidopsis thaliana",
        "a slender upright flowering bolt/stalk",
        "green leaves (a rosette or leaves on the stem)",
        "reproductive_silique",
        "thin elongated upright siliques along the stem",
    ),
    # Kingdom Fungi + a depicted fruit — single-body-plan organisms (see _body_inv).
    "Lycoperdon perlatum": _body_inv(
        "Lycoperdon perlatum",
        "fruiting_body",
        "a rounded pear-/globe-shaped pale fungal fruiting body",
        ("sterile_base", "a short tapered stalk-like base beneath the body"),
        ("surface_ornament", "fine conical warts or granular spines on the surface"),
    ),
    "Cucurbita pepo": _body_inv(
        "Cucurbita pepo",
        "fruit_body",
        "a single rounded gourd/pumpkin fruit",
        ("peduncle", "a short woody stem attached to the fruit"),
        ("surface_ribbing", "vertical ribs or furrows running down the fruit"),
    ),
    "Hericium erinaceus": _body_inv(
        "Hericium erinaceus",
        "fruiting_body",
        "a rounded compact whitish fungal mass",
        ("spines", "dense cascading icicle-like spines/teeth hanging from the mass"),
        ("substrate", "attachment to a piece of wood or bark"),
    ),
}


def inventory_for(taxon: str) -> TaxonInventory | None:
    return ORGAN_INVENTORY.get(taxon)
