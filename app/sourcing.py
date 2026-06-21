"""License classification + depiction labeling for externally-sourced 3D models.

Pure functions, no I/O. See docs/superpowers/specs/2026-06-21-external-model-sourcing-design.md.
Private-tool policy: host any CC/public-domain license (incl. NC/ND); exclude
all-rights-reserved/unmarked. `public_safe` marks the stricter set for the future
pre-public cleanup.
"""

from __future__ import annotations

import re

_CC_MARKERS = ("cc0", "cc-by", "cc by", "creativecommons", "creative commons", "public domain")
# Sketchfab short codes returned by objaverse.load_annotations (license field).
# "by*" codes are all CC licenses; "cc0" is public domain.
_SKETCHFAB_SHORT_CODES = frozenset(("cc0", "by", "by-sa", "by-nc", "by-nc-sa", "by-nc-nd", "by-nd"))
_PUBLIC_SAFE_BAD = ("nc", "nd")  # non-commercial / no-derivatives → not public-safe


def classify_license(license_str: str | None) -> str:
    """'host' for any CC/public-domain license; 'exclude' for ARR/proprietary/unmarked."""
    if not license_str:
        return "exclude"
    s = license_str.strip().lower()
    # Accept Sketchfab short codes (as returned by objaverse.load_annotations)
    if s in _SKETCHFAB_SHORT_CODES:
        return "host"
    return "host" if any(m in s for m in _CC_MARKERS) else "exclude"


def public_safe(license_str: str | None) -> bool:
    """True only for CC0 / CC-BY / CC-BY-SA (no NC, no ND). For the future public re-vet."""
    if classify_license(license_str) != "host":
        return False
    s = license_str.strip().lower()
    # split tokens on non-alpha so 'nc'/'nd' match as license components, not substrings
    parts = set(filter(None, re.split(r"[^a-z0-9]+", s)))
    return not any(bad in parts for bad in _PUBLIC_SAFE_BAD)


def label_depiction(text: str) -> str:
    """Coarse subject label from an object's name/caption."""
    s = (text or "").lower()
    if any(w in s for w in ("plant", "vine", "bush", "seedling", "sapling", "potted")):
        return "whole_plant"
    if "leaf" in s or "foliage" in s:
        return "leaf"
    if (
        any(w in s for w in ("tomato", "fruit", "cherry", "produce"))
        and "can" not in s
        and "soup" not in s
    ):
        return "fruit"
    return "other"


# --- Source classes for the spotlight grid (ai recon / real scan / artist-found) ---
SCAN_DATASETS: dict[str, dict] = {
    "plant3d": {
        "name": "Plant3D (Salk)",
        "license": "CC-BY 4.0",
        "attribution": "Salk Institute / Navlakha lab — Plant3D, Mendeley 10.17632/9k7zctdyhs.1",
        "url": "https://data.mendeley.com/datasets/9k7zctdyhs/1",
    },
    "tomatowur": {
        "name": "TomatoWUR",
        "license": "CC-BY 4.0",
        "attribution": "Wageningen University & Research — TomatoWUR (4TU.ResearchData)",
        "url": "https://data.4tu.nl/",
    },
    "crops3d": {
        "name": "Crops3D",
        "license": "CC-BY-NC-ND 4.0",
        "attribution": "Crops3D (Nature Scientific Data 2024)",
        "url": "https://doi.org/10.1038/s41597-024-04290-0",
    },
}
SCAN_SOURCES = frozenset(SCAN_DATASETS)


def source_class(source: str | None) -> str:
    """Group key for the spotlight: 'ai' (our recon — local or via an image-to-3D API),
    'procedural' (rule-based generators like Infinigen), 'scan' (real scan dataset),
    'found' (artist repos like Objaverse/Sketchfab)."""
    if source == "bio3d-arena" or (source or "").startswith("api:"):
        return "ai"
    if source == "infinigen":
        return "procedural"
    if source in SCAN_SOURCES:
        return "scan"
    return "found"
