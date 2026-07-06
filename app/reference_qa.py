"""Quality-assessment for reference images. Reuses the completeness VLM machinery: a reference
photo of a single organ (e.g. a lone tomato fruit) maps to `derive`'s 'isolated-organ' category
== fruit_only. `fruit_only` is `bool | None`: for a single-required-organ body plan (fungi,
gourd — the fruit/body IS the whole organism) organ-coverage cannot distinguish fruit-only from
complete, so it is `None` (undeterminable; deferred to the CLIP composition mechanism). Uses a
PHOTO-framed prompt, not the 3D-render-sheet framing."""

from __future__ import annotations

from .completeness import COMPLETENESS_TOOL, _parse, derive
from .judge import JUDGE_MODEL
from .organ_inventory import TaxonInventory


def _sniff_media_type(data: bytes) -> str:
    """Declare the Anthropic image media_type from the actual bytes — reference photos are JPEG,
    not PNG, and the API rejects a declared type that doesn't match the bytes (recurring bug)."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return "image/jpeg"  # sensible default: reference photos are overwhelmingly JPEG


def _photo_messages(png: bytes, inventory: TaxonInventory) -> list[dict]:
    import base64

    lines = "\n".join(f"- {o.key}: {o.visual}" for o in inventory.organs)
    b64 = base64.b64encode(png).decode("ascii")
    media_type = _sniff_media_type(png)
    text = (
        f"This is a REAL PHOTOGRAPH intended as a reference for the organism {inventory.taxon}. "
        "For EACH expected organ below, mark whether it is visibly present in THIS photo "
        "(present / absent / uncertain). A close-up of a single organ (e.g. only a fruit or only "
        "a cap) should mark the others absent.\n\n"
        f"Expected organs:\n{lines}\n\nThen call record_completeness."
    )
    return [
        {
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": media_type, "data": b64},
                },
                {"type": "text", "text": text},
            ],
        }
    ]


def assess_organ_coverage(client, photo_png: bytes, *, inventory: TaxonInventory) -> dict:
    resp = client.messages.create(
        model=JUDGE_MODEL,
        max_tokens=500,
        tools=[COMPLETENESS_TOOL],
        tool_choice={"type": "tool", "name": "record_completeness"},
        messages=_photo_messages(photo_png, inventory),
    )
    parsed = _parse(resp)  # {"organs_present": [...], "note": str}
    category, score = derive(inventory, parsed["organs_present"])
    n_required = sum(1 for o in inventory.organs if o.required)
    # derive's 'isolated-organ' only separates from 'complete' when >=2 organs are required
    # (a plant body plan where the reproductive organ is a distinguishable sub-part). For a
    # single-required-organ body plan (_body_inv: fungi, gourd) the fruit/body IS the whole
    # organism, so organ-coverage cannot tell a fruit-only photo from a complete one — defer to
    # the direct VLM composition check `assess_composition` by returning fruit_only=None.
    # (The 2026-07-06 probe showed CLIP composition, binary or multi-class, cannot do this —
    # whole-vs-part is a reasoning judgment, which is the VLM's strength, not CLIP zero-shot's.)
    fruit_only = (category == "isolated-organ") if n_required >= 2 else None
    return {
        "category": category,
        "score": score,
        "organs_present": parsed["organs_present"],
        "note": parsed["note"],
        "fruit_only": fruit_only,
    }


def species_matches(
    bundle, photo_png: bytes, *, claimed_taxon: str, panel: list[str], min_margin: float = 0.0
) -> dict:
    """Multi-class species check (2026-07-06 probe: 13/13). `panel` is the candidate taxa
    (MUST include `claimed_taxon`). Returns {"ok", "top", "prob", "margin"}: ok iff BioCLIP's
    top-1 IS the claimed taxon (and, if min_margin>0, wins by at least that margin). This
    replaces the retired binary species_rep_score."""
    from .species_id import classify_species

    if claimed_taxon not in panel:
        panel = [claimed_taxon, *panel]
    r = classify_species(bundle, photo_png, panel)
    ok = (r["top"] == claimed_taxon) and (r["margin"] >= min_margin)
    return {"ok": ok, "top": r["top"], "prob": r["prob"], "margin": r["margin"]}


COMPOSITION_TOOL = {
    "name": "record_composition",
    "description": "Record whether a reference photo shows the whole organism or only an isolated part.",
    "input_schema": {
        "type": "object",
        "properties": {
            "shows": {"type": "string", "enum": ["whole_organism", "isolated_part"]},
            "note": {"type": "string"},
        },
        "required": ["shows", "note"],
    },
}


def assess_composition(client, photo_png: bytes, *, taxon: str, common: str) -> dict:
    """Direct VLM composition judgment for BODY-PLAN taxa (gourd/fungi) where organ-coverage
    cannot tell fruit-only from complete. Asks whether the photo shows the whole living organism
    in context vs only an isolated/harvested part. Returns {"isolated": bool, "note": str}."""
    import base64

    b64 = base64.b64encode(photo_png).decode("ascii")
    text = (
        f"This is a reference photograph of {common} ({taxon}). Judge its COMPOSITION only. "
        "Does it show the WHOLE living organism in its natural or growing context — a plant with "
        "stems/leaves/roots, or a whole intact fungus on its substrate — or does it show ONLY an "
        "ISOLATED, detached, or harvested part, e.g. a single picked fruit/gourd sitting on a "
        "table or a lone cut mushroom with no body/context? Call record_composition with "
        "'whole_organism' or 'isolated_part'."
    )
    resp = client.messages.create(
        model=JUDGE_MODEL,
        max_tokens=300,
        tools=[COMPOSITION_TOOL],
        tool_choice={"type": "tool", "name": "record_composition"},
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": _sniff_media_type(photo_png),
                            "data": b64,
                        },
                    },
                    {"type": "text", "text": text},
                ],
            }
        ],
    )
    block = next(b for b in resp.content if getattr(b, "type", None) == "tool_use")
    return {
        "isolated": block.input.get("shows") == "isolated_part",
        "note": block.input.get("note", ""),
    }


def qa_reference_image(
    *, organ: dict, composition: dict | None = None, species: dict | None = None
) -> dict:
    """Combine the QA signals into a pass/fail verdict. `organ` = assess_organ_coverage output
    (plant-taxa fruit-only via fruit_only=True); `composition` = assess_composition output
    (body-plan fruit-only via isolated=True); `species` = species_matches output (mismatch via
    ok=False). Any triggered signal fails the image."""
    reasons: list[str] = []
    if organ.get("fruit_only"):
        reasons.append("fruit-only / isolated-organ reference (organ-coverage)")
    if organ.get("category") == "fragment":
        reasons.append("fragment — no expected organ visible")
    if composition is not None and composition.get("isolated"):
        reasons.append("isolated part, not the whole organism (VLM composition)")
    if species is not None and not species.get("ok", True):
        reasons.append(f"species mismatch — reads as {species.get('top')!r}, not the claimed taxon")
    return {"passed": not reasons, "reasons": reasons}
