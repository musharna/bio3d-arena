"""Multi-axis geometric-difficulty rubric for benchmark taxa.

Grounded axes: fine_detail (Dora-Bench salient-edge-density, arXiv 2412.17808),
self_occlusion + non_rigidity (Yunus et al., "Recent Trends in 3D Reconstruction of
General Non-Rigid Scenes", CGF 2024, arXiv 2403.15064), and topology + thin_structure
(ours, unclaimed for organisms). Each axis is scored 0..2; the sum maps to a difficulty
tier. Pure module — no DB, exhaustively unit-tested. Difficulty is a property of the
TAXON (not a specimen mesh); v1 scores are hand-authored against the cited axis
definitions. Computed corroboration of the computable axes is a deferred follow-on.
"""

from __future__ import annotations

AXES: tuple[str, ...] = (
    "fine_detail",
    "self_occlusion",
    "non_rigidity",
    "topology",
    "thin_structure",
)

# Sum cut points (inclusive) → tier. Max sum = 2 * len(AXES) = 10.
_EASY_MAX = 3
_MODERATE_MAX = 6


def tier_for_scores(scores: dict[str, int]) -> str:
    """Validate all AXES present and each an int in 0..2, sum, map to a tier. Fail-loud."""
    missing = [a for a in AXES if a not in scores]
    if missing:
        raise ValueError(f"missing axis scores: {missing}")
    extra = [a for a in scores if a not in AXES]
    if extra:
        raise ValueError(f"unknown axis keys: {extra}")
    for a in AXES:
        v = scores[a]
        if not isinstance(v, int) or v < 0 or v > 2:
            raise ValueError(f"axis {a!r} score must be an int in 0..2, got {v!r}")
    total = sum(scores[a] for a in AXES)
    if total <= _EASY_MAX:
        return "easy"
    if total <= _MODERATE_MAX:
        return "moderate"
    return "hard"


# Hand-authored v1 scores + rationale per in-scope taxon (species_slug matches
# ReconTask.species_slug / the title binomial). Comment shows the tier + sum.
RUBRIC: dict[str, dict] = {
    "solanum_lycopersicum": {  # sum 3 → easy
        "scores": {
            "fine_detail": 1,
            "self_occlusion": 1,
            "non_rigidity": 1,
            "topology": 0,
            "thin_structure": 0,
        },
        "rationale": {
            "fine_detail": "large leaves and fruit dominate; only modest leaf serration",
            "self_occlusion": "moderate leaf overlap on a bushy but open habit",
            "non_rigidity": "leaves flex but the compact plant is self-supporting",
            "topology": "single connected bush, few through-holes",
            "thin_structure": "thick stems, broad leaves, large fruit — no fine filaments",
        },
    },
    "zea_mays": {  # sum 4 → moderate
        "scores": {
            "fine_detail": 1,
            "self_occlusion": 1,
            "non_rigidity": 1,
            "topology": 0,
            "thin_structure": 1,
        },
        "rationale": {
            "fine_detail": "tassel and silk carry fine detail over broad blade leaves",
            "self_occlusion": "arching blade leaves overlap moderately",
            "non_rigidity": "long blade leaves bend and curl",
            "topology": "single stalk, connected",
            "thin_structure": "thin tassel/silk and blade-leaf edges",
        },
    },
    "glycine_max": {  # sum 6 → moderate
        "scores": {
            "fine_detail": 1,
            "self_occlusion": 2,
            "non_rigidity": 1,
            "topology": 1,
            "thin_structure": 1,
        },
        "rationale": {
            "fine_detail": "trifoliate leaves and pods; pubescence is fine but not dominant",
            "self_occlusion": "dense bushy canopy with heavily overlapping leaves",
            "non_rigidity": "broad leaflets flop on thin petioles",
            "topology": "branching stems add moderate branch complexity",
            "thin_structure": "thin petioles and stems, pod edges",
        },
    },
    "arabidopsis_thaliana": {  # sum 7 → hard
        "scores": {
            "fine_detail": 2,
            "self_occlusion": 1,
            "non_rigidity": 1,
            "topology": 1,
            "thin_structure": 2,
        },
        "rationale": {
            "fine_detail": "small rosette leaves plus tiny siliques and flowers",
            "self_occlusion": "flat rosette is fairly open; bolting stems mostly exposed",
            "non_rigidity": "thin bolting stems flex",
            "topology": "branching inflorescence adds branch complexity",
            "thin_structure": "very thin bolting stems and small parts",
        },
    },
    "pinus_sylvestris": {  # sum 7 → hard
        "scores": {
            "fine_detail": 2,
            "self_occlusion": 2,
            "non_rigidity": 0,
            "topology": 1,
            "thin_structure": 2,
        },
        "rationale": {
            "fine_detail": "thousands of fine needles — extreme repeated detail",
            "self_occlusion": "dense needle and branch canopy, heavy occlusion",
            "non_rigidity": "woody and rigid — needles/branches barely deform",
            "topology": "branching woody structure",
            "thin_structure": "needles are the definitional thin structure",
        },
    },
    "rosa": {  # sum 7 → hard
        "scores": {
            "fine_detail": 2,
            "self_occlusion": 2,
            "non_rigidity": 1,
            "topology": 1,
            "thin_structure": 1,
        },
        "rationale": {
            "fine_detail": "layered petals, serrated leaflets, and thorns",
            "self_occlusion": "dense bushy shrub; petals occlude the flower interior",
            "non_rigidity": "leaves and blooms flex",
            "topology": "branching canes with multiple blooms",
            "thin_structure": "thorns and thin stems over moderate woody canes",
        },
    },
    "hordeum_vulgare": {  # sum 9 → hard  (root-system MRI task)
        "scores": {
            "fine_detail": 2,
            "self_occlusion": 2,
            "non_rigidity": 1,
            "topology": 2,
            "thin_structure": 2,
        },
        "rationale": {
            "fine_detail": "fine lateral roots and root hairs",
            "self_occlusion": "dense root network crossing and overlapping in soil",
            "non_rigidity": "roots are flexible though the MRI capture is static",
            "topology": "highly branching network — many branches / high genus",
            "thin_structure": "roots are thin filamentous structures throughout",
        },
    },
    # --- Kingdom Fungi + easy-plant expansion (fills the corpus hard-skew) ---
    "cucurbita_pepo": {  # sum 0 → easy  (tier-0 floor)
        "scores": {
            "fine_detail": 0,
            "self_occlusion": 0,
            "non_rigidity": 0,
            "topology": 0,
            "thin_structure": 0,
        },
        "rationale": {
            "fine_detail": "smooth ribbed convex fruit, no fine repeated detail",
            "self_occlusion": "single convex gourd, nothing occludes itself",
            "non_rigidity": "rigid firm fruit",
            "topology": "one solid connected body, no holes",
            "thin_structure": "thick-walled fruit, no filaments",
        },
    },
    "lycoperdon_perlatum": {  # sum 1 → easy
        "scores": {
            "fine_detail": 1,
            "self_occlusion": 0,
            "non_rigidity": 0,
            "topology": 0,
            "thin_structure": 0,
        },
        "rationale": {
            "fine_detail": "granular conical warts on the surface",
            "self_occlusion": "convex globe, no self-occlusion",
            "non_rigidity": "firm solid fruiting body",
            "topology": "single connected blob",
            "thin_structure": "no filaments or thin parts",
        },
    },
    "hericium_erinaceus": {  # sum 8 → hard
        "scores": {
            "fine_detail": 2,
            "self_occlusion": 2,
            "non_rigidity": 1,
            "topology": 1,
            "thin_structure": 2,
        },
        "rationale": {
            "fine_detail": "hundreds of pendant spines",
            "self_occlusion": "spines hang behind spines, heavy occlusion",
            "non_rigidity": "soft drooping spines",
            "topology": "one mass with many appendages",
            "thin_structure": "cascading spines are the definitional thin structure",
        },
    },
}


def taxon_axes(species_slug: str) -> dict[str, int]:
    """The 0..2 axis scores for a taxon. Fail-loud on unknown taxon."""
    entry = RUBRIC.get(species_slug)
    if entry is None:
        raise ValueError(f"no rubric entry for taxon {species_slug!r}")
    return dict(entry["scores"])


def taxon_tier(species_slug: str) -> str:
    """The difficulty tier for a taxon (validates its scores). Fail-loud on unknown taxon."""
    return tier_for_scores(taxon_axes(species_slug))
