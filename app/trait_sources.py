"""Rubric trait sources: Wikidata (db tier) + retrieval-grounded literature (llm tier).

Pure cores with INJECTED network functions (mirrors app/judge.py / app/traits.py) so units
test without network. scripts/build_trait_rubrics.py wires the real SPARQL / Europe PMC /
Anthropic clients behind --live. Every emitted trait carries a resolvable citation; no trait
claim or citation comes from model recall."""

from __future__ import annotations

from .judge import JUDGE_MODEL
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


# ---------------------------------------------------------------------------
# llm tier: retrieval-grounded extraction
# ---------------------------------------------------------------------------

EXTRACT_TOOL = {
    "name": "record_extracted_traits",
    "description": "Record botanical traits EXPLICITLY STATED in the provided source text.",
    "input_schema": {
        "type": "object",
        "properties": {
            "traits": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string", "description": "short snake_case id"},
                        "trait_class": {"type": "string", "enum": sorted(SCORED_CLASSES)},
                        "expected": {
                            "type": "string",
                            "description": "the expected visible value, e.g. 'red', 'compound'",
                        },
                        "quote": {
                            "type": "string",
                            "description": "VERBATIM span from the source text stating this trait",
                        },
                    },
                    "required": ["key", "trait_class", "expected", "quote"],
                },
            }
        },
        "required": ["traits"],
    },
}


def build_extract_messages(taxon: str, source_text: str) -> list[dict]:
    text = (
        f"Below is source text about the plant {taxon}. Extract only VISUALLY-OBSERVABLE "
        "morphological traits the text EXPLICITLY states (color, organ shape, leaf arrangement, "
        "inflorescence, presence of structures, relative proportions). For each trait give a "
        "short key, a trait_class, the expected visible value, and a VERBATIM quote from the "
        "text. Do not infer traits the text does not state. Call record_extracted_traits.\n\n"
        f"Source text:\n{source_text}"
    )
    return [{"role": "user", "content": [{"type": "text", "text": text}]}]


def parse_extracted(resp, source_text: str, *, citation: str, source_detail: str) -> list[dict]:
    for block in getattr(resp, "content", []):
        if (
            getattr(block, "type", None) == "tool_use"
            and getattr(block, "name", "") == "record_extracted_traits"
        ):
            out: list[dict] = []
            for r in (block.input or {}).get("traits", []):
                tc = r.get("trait_class")
                quote = r.get("quote", "")
                if tc not in SCORED_CLASSES:
                    continue
                if not quote or quote not in source_text:  # anti-hallucination: must be verbatim
                    continue
                out.append(
                    {
                        "key": r["key"],
                        "trait_class": tc,
                        "type": "categorical",
                        "expected": r.get("expected", ""),
                        "visual": True,
                        "citation": citation,
                        "source_detail": source_detail,
                        "quote": quote,
                    }
                )
            return out
    return []


def literature_grounded_traits(
    taxon: str, *, search_fn, resolve_fn, llm_client, max_pubs: int = 5
) -> list[dict]:
    """llm-tier traits: for each of up to max_pubs publications, resolve its source text and
    have the LLM extract only traits it can quote from that text. Citation = the publication."""
    traits: list[dict] = []
    seen_keys: set[str] = set()
    for pub in (search_fn(taxon) or [])[:max_pubs]:
        text = resolve_fn(pub)
        if not text:
            continue
        citation = pub.get("doi") or pub.get("pmid") or pub.get("title")
        if not citation:
            continue
        resp = llm_client.messages.create(
            model=JUDGE_MODEL,
            max_tokens=1500,
            tools=[EXTRACT_TOOL],
            tool_choice={"type": "tool", "name": "record_extracted_traits"},
            messages=build_extract_messages(taxon, text),
        )
        for t in parse_extracted(resp, text, citation=str(citation), source_detail=str(citation)):
            if t["key"] in seen_keys:
                continue
            seen_keys.add(t["key"])
            traits.append(t)
    return traits


# ---------------------------------------------------------------------------
# citation verification gate
# ---------------------------------------------------------------------------


def _is_wikidata(citation: str) -> bool:
    return "wikidata.org" in (citation or "")


def verify_citations(traits, *, ghostcite_fn, resolve_fn) -> list[dict]:
    """Drop any trait whose citation can't be verified. Wikidata URLs must resolve;
    paper citations must be ghostcite-verified AND not retracted."""
    kept: list[dict] = []
    for t in traits:
        cite = t.get("citation") or ""
        if _is_wikidata(cite):
            if resolve_fn(cite):
                kept.append(t)
            continue
        res = ghostcite_fn(cite) or {}
        if res.get("verified") and not res.get("retracted"):
            kept.append(t)
    return kept
