# app/organ_inventory.py
"""Authored per-taxon expected-organ inventories for the organism-level completeness metric.

Required organs = the vegetative body (a plant is "complete" if it has an axis + foliage).
The reproductive organ is OPTIONAL so a lone fruit/cone/pod registers as an isolated organ,
not a complete plant. Visual descriptors are image-judgeable phrases for the VLM checklist.
Taxon keys MUST match app.trait_morphology.MORPHOLOGY_TRAITS (== TraitRubric.taxon)."""

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


ORGAN_INVENTORY: dict[str, TaxonInventory] = {
    "Solanum lycopersicum": _inv(
        "Solanum lycopersicum",
        "an upright central green stem",
        "compound green leaves along the stem",
        "reproductive_fruit",
        "round red/green fleshy berries",
    ),
    "Zea mays": _inv(
        "Zea mays",
        "a tall single vertical stalk",
        "long linear strap-like blades along the stalk",
        "reproductive_inflorescence",
        "a terminal tassel and/or a lateral ear",
    ),
    "Pinus sylvestris": _inv(
        "Pinus sylvestris",
        "a woody trunk with branches",
        "needle leaves in clusters on the branches",
        "reproductive_cone",
        "egg/cone-shaped woody cones",
    ),
    "Rosa": _inv(
        "Rosa",
        "a thorny woody stem",
        "pinnate serrated green leaves",
        "reproductive_flower_hip",
        "a flower and/or a rounded fleshy rose hip",
    ),
    "Glycine max": _inv(
        "Glycine max",
        "an erect branching stem",
        "trifoliate leaves (leaves with three leaflets)",
        "reproductive_pod",
        "narrow fuzzy seed pods",
    ),
    "Arabidopsis thaliana": _inv(
        "Arabidopsis thaliana",
        "a slender upright flowering bolt/stalk",
        "a basal rosette of leaves",
        "reproductive_silique",
        "thin elongated upright siliques along the stem",
    ),
}


def inventory_for(taxon: str) -> TaxonInventory | None:
    return ORGAN_INVENTORY.get(taxon)
