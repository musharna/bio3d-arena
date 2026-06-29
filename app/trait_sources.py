"""Rubric trait sources: Wikidata (db tier) + retrieval-grounded literature (llm tier).

Pure cores with INJECTED network functions (mirrors app/judge.py / app/traits.py) so units
test without network. scripts/build_trait_rubrics.py wires the real SPARQL / Europe PMC /
Anthropic clients behind --live. Every emitted trait carries a resolvable citation; no trait
claim or citation comes from model recall."""

from __future__ import annotations

from .traits import SCORED_CLASSES

# Verified Wikidata morphology properties (probed live 2026-06-29) → (trait_class, key).
# Wikidata is a thin, high-confidence backbone; most visual traits come from the llm tier.
WIKIDATA_PROPERTY_MAP: dict[str, tuple[str, str]] = {
    "P4000": ("organ_shape", "wd_fruit_type"),  # has fruit type
    "P12616": ("organ_shape", "wd_leaf_morphology"),  # leaf morphology
    "P3739": ("inflorescence", "wd_inflorescence"),  # inflorescence
    "P2827": ("color", "wd_flower_color"),  # flower color
}


def wikidata_traits(taxon: str, *, sparql_fn) -> list[dict]:
    """db-tier traits from the taxon's Wikidata item. `sparql_fn(taxon)` returns
    {"qid": "Q23501", "props": {"P4000": "berry", ...}} or None when the taxon has no item.
    Maps only WIKIDATA_PROPERTY_MAP entries that are present and non-empty."""
    rec = sparql_fn(taxon)
    if not rec or not rec.get("qid"):
        return []
    qid = rec["qid"]
    citation = f"https://www.wikidata.org/wiki/{qid}"
    out: list[dict] = []
    for pid, value in (rec.get("props") or {}).items():
        mapping = WIKIDATA_PROPERTY_MAP.get(pid)
        if mapping is None or not value:
            continue
        trait_class, key = mapping
        if trait_class not in SCORED_CLASSES:  # defensive: map must stay valid
            continue
        out.append(
            {
                "key": key,
                "trait_class": trait_class,
                "type": "categorical",
                "expected": value,
                "visual": True,
                "citation": citation,
                "source_detail": qid,
                "quote": f"{pid}={value}",
            }
        )
    return out
