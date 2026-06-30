"""Authored, visually-judgeable morphology rubrics for the 6 Mode-C study taxa.

These replace the literature-extraction trait set (which produced genetics/temporal/
microscopic/wrong-organism traits unfit for judging a static 3D model). Every trait here
is a static, macroscopic, externally-visible, absolute morphological feature, authored
from descriptive botany and grounded: db-tier values come from Wikidata structured
morphology (via build_morphology_rubric's sparql_fn); the remaining ref-tier traits cite
the taxon's Wikispecies page (resolve-verified at load). See the 2026-06-30 spec."""

from __future__ import annotations

from .trait_sources import wikidata_traits
from .traits import judgeable_reason

WIKISPECIES = "https://species.wikimedia.org/wiki/"


def WIKISPECIES_URL(taxon: str) -> str:
    return WIKISPECIES + taxon.replace(" ", "_")


# Authored ref-tier traits. Each: key, trait_class, type, expected, visual.
# (source_tier + citation are stamped by build_morphology_rubric.)
def _t(key, cls, expected, visual):
    return {
        "key": key,
        "trait_class": cls,
        "type": "qualitative",
        "expected": expected,
        "visual": visual,
    }


MORPHOLOGY_TRAITS: dict[str, list[dict]] = {
    "Solanum lycopersicum": [
        _t(
            "plant_habit",
            "habit",
            "sprawling herbaceous plant",
            "weak sprawling/decumbent stems, not an upright woody tree",
        ),
        _t(
            "leaf_form",
            "organ_shape",
            "pinnately compound leaf",
            "leaf divided into leaflets along a central axis",
        ),
        _t("fruit_form", "organ_shape", "globose berry", "round fleshy fruit"),
        _t("fruit_color_ripe", "color", "red ripe fruit", "ripe fruit is red"),
        _t("flower_color", "color", "yellow flower", "small yellow star-shaped flowers"),
        _t(
            "inflorescence_form",
            "inflorescence",
            "branched cyme",
            "small branched flower clusters off the stem",
        ),
        _t(
            "leaf_arrangement",
            "phyllotaxy",
            "alternate leaves",
            "leaves attach singly, alternating along the stem",
        ),
        _t("fruit_present", "presence", "fruits present", "one or more fruits on the plant"),
        _t(
            "plant_proportion",
            "proportion",
            "medium height plant",
            "knee-to-head height, taller than a seedling, not a tree",
        ),
    ],
    "Zea mays": [
        _t("plant_habit", "habit", "tall single-culm grass", "one thick erect unbranched stalk"),
        _t("leaf_form", "organ_shape", "long linear leaf", "long narrow strap-like blades"),
        _t(
            "leaf_arrangement",
            "phyllotaxy",
            "alternate distichous leaves",
            "leaves alternate in two ranks up the culm",
        ),
        _t(
            "tassel_form",
            "inflorescence",
            "terminal branched tassel",
            "branched spike of male flowers at the very top",
        ),
        _t(
            "ear_present",
            "presence",
            "lateral ear present",
            "a cob/ear emerging from a leaf axil on the side of the stalk",
        ),
        _t("foliage_color", "color", "green foliage", "green leaves"),
        _t(
            "stem_form",
            "organ_shape",
            "single solid unbranched culm",
            "one thick stem, no woody branching",
        ),
        _t("plant_proportion", "proportion", "tall plant", "tall, much taller than wide"),
    ],
    "Pinus sylvestris": [
        _t(
            "plant_habit",
            "habit",
            "excurrent conifer tree",
            "single straight trunk with a conical/columnar crown",
        ),
        _t(
            "needle_form", "organ_shape", "needle leaf", "thin needle-like leaves, not broad blades"
        ),
        _t(
            "needle_arrangement",
            "phyllotaxy",
            "needles in fascicles of two",
            "needles held in paired bundles",
        ),
        _t("cone_form", "organ_shape", "ovoid woody cone", "egg/cone-shaped woody cones"),
        _t("cone_present", "presence", "cones present", "cones on the branches"),
        _t("foliage_color", "color", "blue-green foliage", "glaucous blue-green needles"),
        _t(
            "bark_color",
            "color",
            "orange-brown upper bark",
            "orange/reddish flaky bark on the upper trunk",
        ),
        _t("plant_proportion", "proportion", "tall tree", "a tall tree, much taller than a shrub"),
    ],
    "Rosa": [
        _t(
            "plant_habit",
            "habit",
            "woody shrub with arching canes",
            "woody shrub or climber with arching thorny stems",
        ),
        _t(
            "leaf_form",
            "organ_shape",
            "odd-pinnate serrate leaf",
            "compound leaf with toothed leaflets",
        ),
        _t("fruit_form", "organ_shape", "rose hip", "rounded fleshy hips"),
        _t(
            "flower_pigmentation",
            "color",
            "pigmented petals",
            "showy colored petals (pink/red/white/yellow), not green",
        ),
        _t(
            "inflorescence_form",
            "inflorescence",
            "solitary or small corymb",
            "flowers single or in small clusters at shoot tips",
        ),
        _t(
            "prickles_present",
            "presence",
            "prickles present on stems",
            "thorns/prickles along the canes",
        ),
        _t(
            "leaf_arrangement",
            "phyllotaxy",
            "alternate leaves",
            "leaves attach singly, alternating along the stem",
        ),
        _t(
            "plant_proportion",
            "proportion",
            "shrub-sized plant",
            "shrub scale, not a tall tree or a tiny herb",
        ),
    ],
    "Glycine max": [
        _t("plant_habit", "habit", "erect bushy herb", "upright branched hairy herbaceous plant"),
        _t("leaf_form", "organ_shape", "trifoliate leaf", "leaves with three leaflets"),
        _t(
            "inflorescence_form",
            "inflorescence",
            "axillary raceme",
            "small flower clusters in the leaf axils",
        ),
        _t("pod_present", "presence", "pods present", "fuzzy seed pods on the plant"),
        _t(
            "flower_color",
            "color",
            "white to purple flower",
            "small white or purple pea-like flowers",
        ),
        _t(
            "leaf_arrangement",
            "phyllotaxy",
            "alternate leaves",
            "leaves attach singly, alternating along the stem",
        ),
        _t("pod_form", "organ_shape", "elongate pod", "narrow slightly curved pods"),
        _t("plant_proportion", "proportion", "medium height plant", "knee-to-waist height bush"),
    ],
    "Arabidopsis thaliana": [
        _t(
            "plant_habit",
            "habit",
            "rosette with erect flowering stalk",
            "low basal leaf rosette plus a thin tall flowering stem",
        ),
        _t(
            "rosette_leaf_form",
            "organ_shape",
            "spatulate rosette leaf",
            "small spoon/oblong-shaped basal leaves",
        ),
        _t("inflorescence_form", "inflorescence", "raceme", "flowers spaced along the upper stem"),
        _t("flower_color", "color", "white flower", "small white four-petalled flowers"),
        _t("fruit_form", "organ_shape", "slender silique", "thin elongated seed pods"),
        _t("silique_present", "presence", "siliques present", "narrow upright pods along the stem"),
        _t(
            "leaf_arrangement",
            "phyllotaxy",
            "basal rosette leaves",
            "most leaves in a flat basal rosette",
        ),
        _t("plant_proportion", "proportion", "small plant", "small plant, ankle-to-knee height"),
    ],
}


def build_morphology_rubric(taxon: str, *, sparql_fn) -> list[dict]:
    """Assemble taxon's rubric: Wikidata db-tier traits first, then authored ref-tier
    traits, deduped on (trait_class, expected.lower()) with db preferred. Every trait is
    stamped with source_tier+citation and validated (schema-judgeable). Raises on an
    unknown taxon or a non-judgeable authored trait (author bug)."""
    if taxon not in MORPHOLOGY_TRAITS:
        raise ValueError(f"no authored morphology for {taxon!r}")
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for t in wikidata_traits(
        taxon, sparql_fn=sparql_fn
    ):  # stamp source_tier=db (wikidata_traits omits it)
        t = {**t, "source_tier": "db"}
        key = (t["trait_class"], (t["expected"] or "").strip().lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(t)

    for base in MORPHOLOGY_TRAITS[taxon]:
        t = {**base, "source_tier": "ref", "citation": WIKISPECIES_URL(taxon)}
        key = (t["trait_class"], (t["expected"] or "").strip().lower())
        if key in seen:
            continue
        reason = judgeable_reason({**t, "taxon": taxon})
        if reason is not None:
            raise ValueError(f"authored trait {t['key']!r} not judgeable: {reason}")
        seen.add(key)
        out.append(t)
    return out
