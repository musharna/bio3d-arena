"""License semantics: the redistribution allowlist, plus normalization of loose license labels to
SPDX ids so the fail-loud gates pass legitimately-redistributable assets that were merely labelled
in a loose/space form (e.g. 'CC-BY 4.0' -> 'CC-BY-4.0'). Normalization never widens the allowlist:
NC/ND/unknown strings normalize to a non-allowlisted form and still fail the gate."""

from __future__ import annotations

import re

# The single answer to "may we redistribute this?" — for a generated output AND for the reference
# photo a recon derives from. One set because it is one legal question: export_public runs the
# reference-photo gate only under posture 'redistribute' (display doesn't redistribute the photo).
#
# It was previously written twice as hand-maintained literals — public_export.REDISTRIBUTABLE_
# LICENSES and reference_provenance._CC_OK — which drifted in BOTH directions: the export copy
# admitted CC-BY-2.0 / PUBLIC-DOMAIN / ODbL-1.0 that the reference copy rejected, while the
# reference copy admitted CC-BY-SA-3.0 that the export copy rejected. Nothing failed, because the
# one test covering it restated the literal instead of importing it. Import this; never re-declare
# it. Membership rule: the license must PERMIT redistribution. Attribution and share-alike are
# conditions we satisfy (we ship attribution); NC and ND are bars, so they can never appear here.
REDISTRIBUTABLE_LICENSES = frozenset(
    {
        "CC0-1.0",
        "PUBLIC-DOMAIN",
        "CC-BY-2.0",
        "CC-BY-3.0",
        "CC-BY-4.0",
        "CC-BY-SA-3.0",
        "CC-BY-SA-4.0",
        "ODbL-1.0",
    }
)

# Objaverse/Sketchfab short codes -> CC family (version 4.0 is the Sketchfab default).
_OBJAVERSE_CODES = {
    "by": "CC-BY-4.0",
    "cc0": "CC0-1.0",
    "by-sa": "CC-BY-SA-4.0",
    "by-nc": "CC-BY-NC-4.0",
    "by-nd": "CC-BY-ND-4.0",
    "by-nc-sa": "CC-BY-NC-SA-4.0",
    "by-nc-nd": "CC-BY-NC-ND-4.0",
}


def normalize_license(raw: str | None) -> str | None:
    """Map a loose license label to an SPDX-style id. None/empty -> None. Deterministic:
    lowercase-match short codes; else uppercase, strip a trailing parenthetical (e.g.
    '... (Sketchfab, author)'), collapse internal whitespace/underscores to '-', map CC0."""
    if raw is None:
        return None
    s = raw.strip()
    if not s:
        return None
    low = s.lower()
    if low in _OBJAVERSE_CODES:
        return _OBJAVERSE_CODES[low]
    # Drop a trailing parenthetical note: "CC-BY 4.0 (Sketchfab, foo)" -> "CC-BY 4.0"
    s = re.sub(r"\s*\(.*\)\s*$", "", s).strip()
    up = s.upper()
    # collapse spaces/underscores between tokens to hyphens
    up = re.sub(r"[\s_]+", "-", up)
    # 'CC0' or 'CC0-1.0' -> 'CC0-1.0'
    if up in ("CC0", "CC0-1.0"):
        return "CC0-1.0"
    if up == "PUBLIC-DOMAIN":
        return "PUBLIC-DOMAIN"
    return up
