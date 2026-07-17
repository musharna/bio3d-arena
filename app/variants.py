# app/variants.py
"""Which leaderboard entrants are the SAME model wearing a different hat.

The image→3D board lists TRELLIS three times — `trellis`, `fal:trellis`, `replicate:trellis` — one
model on three hosts. Ranked independently they take three separate rank slots and read as three
competitors, pushing genuine rivals down the board. A variant now renders INDENTED under its
family's representative and consumes no rank.

A variant is the same model served or configured differently — a re-host, or (Spec 2) the same
model at a different effort/quality setting. It is NOT a different version: Hunyuan3D v2, v3 and
3.1 are genuinely different models and stay separate entrants, as does TRELLIS 2 vs TRELLIS. When
in doubt, do not group: wrongly merging two distinct models hides a real result, while wrongly
splitting a re-host only costs a row.

The family's representative is ELECTED at render time (highest BT among the members actually on
this board) rather than pinned in the map. That matters: the local `trellis` checkpoint has no row
on the live board, so a map that named it the parent would have grouped nothing at all while
looking correct.

Static map, mirroring app/kingdoms.py and app/paradigms.py: small, closed, unit-testable, no
migration and no schema column to drift.

Scores are NOT merged. Every variant keeps its own Bradley-Terry fit and its own votes; the
grouping is presentational. Pooling votes across hosts would be a claim we have not tested.
"""

from __future__ import annotations

# slug -> family key. Members of a family are the same underlying model.
FAMILY_OF: dict[str, str] = {
    # TRELLIS: one model, three hosts (local checkpoint, fal, Replicate).
    "trellis": "trellis",
    "fal:trellis": "trellis",
    "replicate:trellis": "trellis",
    # GPT-5.6 Sol at two EFFORT tiers. OpenRouter states outright that "GPT-5.6 Sol Pro is the same
    # underlying model as GPT-5.6 Sol" — the -pro suffix buys more effort, not different weights.
    # This is the effort/quality variant case the grouping was built for: "does more effort produce
    # a better mesh?" is a real question, and the two must not read as two competitors.
    # (commission.slug_for_model: "openrouter-" + the model id, non-alphanumerics -> "-".)
    "openrouter-openai-gpt-5-6-sol": "openrouter-openai-gpt-5-6-sol",
    "openrouter-openai-gpt-5-6-sol-pro": "openrouter-openai-gpt-5-6-sol",
}


def family_of(slug: str) -> str:
    """The family this entrant belongs to (itself, when it has no known siblings)."""
    return FAMILY_OF.get(slug, slug)


def has_family(slug: str) -> bool:
    """True if this slug is a known re-host / config sibling of another entrant."""
    return slug in FAMILY_OF


def nest_variants(rows: list[dict]) -> list[dict]:
    """Fold same-model siblings under one representative row and return the top-level rows.

    Within a family, the highest-BT member PRESENT on this board becomes the representative and the
    rest become its `children`. Electing from what is actually shown means a family still collapses
    when some members are missing (rated-only filter, or a member with no row at all).

    Ranks and medals are RECOMPUTED over the top level. Without that, the folded siblings would
    still consume rank slots — the very distortion being fixed — and leave gaps where they were
    removed. A variant never holds a rank or a medal, but keeps its own score and votes.
    """
    from . import ranking

    families: dict[str, list[dict]] = {}
    for r in rows:
        families.setdefault(family_of(r.get("slug") or ""), []).append(r)

    top: list[dict] = []
    for members in families.values():
        members.sort(key=lambda x: x["bt_score"], reverse=True)
        rep, rest = members[0], members[1:]
        rep["variant_of"] = None
        top.append(rep)
        for child in rest:
            child["variant_of"] = rep.get("slug")
            child["podium"] = False  # a variant holds no rank, so it can claim no medal
            rep.setdefault("children", []).append(child)

    top.sort(key=lambda x: x["bt_score"], reverse=True)
    ranks = ranking.rank_by_ci([(r["bt_lower"], r["bt_upper"]) for r in top])
    for row, rank in zip(top, ranks):
        row["rank"] = rank

    # Same rule as _enrich_leaderboard_rows: a medal is a claim of SEPARATION — a top-3 rank that
    # no other displayed row shares, backed by real votes.
    counts: dict[int, int] = {}
    for r in top:
        counts[r["rank"]] = counts.get(r["rank"], 0) + 1
    for r in top:
        r["podium"] = r.get("n_games", 0) > 0 and r["rank"] <= 3 and counts[r["rank"]] == 1
    return top
