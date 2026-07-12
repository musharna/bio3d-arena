"""The paradigm dimension: which 3D-creation approach a generator uses. Pure module (no DB)
so the vocabulary + predicates are unit-tested and shared by the model, backfill, matchmaking,
rating aggregation, and UI. Ranking is ALWAYS within a single paradigm value."""

from __future__ import annotations

# Present in the data today (backfill assigns these):
BACKFILL_PARADIGMS: tuple[str, ...] = (
    "image_recon",
    "capture_scan",
    "procedural_llm",
    "procedural_expert",
    "retrieval",
)
# Reserved so the enum is stable as future tracks land:
_RESERVED: tuple[str, ...] = ("text_native", "video", "texturing", "agentic", "sketch")
PARADIGMS: tuple[str, ...] = BACKFILL_PARADIGMS + _RESERVED

DISPLAY_NAMES: dict[str, str] = {
    "image_recon": "Image→3D reconstruction",
    "capture_scan": "Scan / capture",
    "procedural_llm": "LLM procedural (code-gen)",
    "procedural_expert": "Expert / simulation procedural",
    "retrieval": "Retrieved asset",
    "text_native": "Text→3D (native)",
    "video": "Video→3D / 4D",
    "texturing": "Texturing / editing",
    "agentic": "Agentic 3D",
    "sketch": "Sketch→3D",
}

# Short labels for the compact segmented tab control on /leaderboard — the full DISPLAY_NAMES
# are long enough that 7 of them wrap the pill row onto two lines (the design's is one tight row).
SHORT_NAMES: dict[str, str] = {
    "image_recon": "Image→3D",
    "capture_scan": "Scan",
    "procedural_llm": "LLM proc.",
    "procedural_expert": "Expert proc.",
    "retrieval": "Retrieved",
    "text_native": "Text→3D",
    "video": "Video→3D",
    "texturing": "Texturing",
    "agentic": "Agentic",
    "sketch": "Sketch→3D",
}


# One plain-language line per modality for the leaderboard hub cards / board headers.
WHAT_THIS_MEASURES: dict[str, str] = {
    "image_recon": "A single photo reconstructed into a 3D mesh.",
    "text_native": "A text prompt turned directly into 3D.",
    "procedural_llm": "An LLM writes Blender/CAD code that builds the 3D model.",
    "agentic": "An agent renders, critiques, and revises its own 3D model in a loop.",
    "capture_scan": "A real organism captured to 3D by photogrammetry / scanning.",
    "video": "Video frames lifted into 3D / 4D.",
    "texturing": "Editing or texturing an existing 3D model.",
    "retrieval": "A pre-existing human-made asset retrieved from a library.",
    "procedural_expert": "Hand-authored rule-based / simulation generators.",
    "sketch": "A sketch turned into 3D.",
}


def is_valid_paradigm(p: str) -> bool:
    return p in PARADIGMS


def same_paradigm(a: str, b: str) -> bool:
    """True iff two paradigm values are equal. Empty==empty is True (all-untagged generators
    form one group pre-backfill, keeping matchmaking backward-compatible); empty vs a tagged
    value is False so a half-backfilled state never silently crosses paradigms."""
    return a == b
