"""License-string normalization to SPDX ids, so the fail-loud allowlist in public_export
passes legitimately-redistributable assets that were merely labelled in a loose/space form
(e.g. 'CC-BY 4.0' -> 'CC-BY-4.0'). Never widens the allowlist: NC/ND/unknown strings normalize
to a non-allowlisted form and still fail the gate."""

from __future__ import annotations

import re

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
