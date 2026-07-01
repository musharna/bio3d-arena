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


def is_valid_paradigm(p: str) -> bool:
    return p in PARADIGMS


def same_paradigm(a: str, b: str) -> bool:
    """True iff two paradigm values are equal. Empty==empty is True (all-untagged generators
    form one group pre-backfill, keeping matchmaking backward-compatible); empty vs a tagged
    value is False so a half-backfilled state never silently crosses paradigms."""
    return a == b
