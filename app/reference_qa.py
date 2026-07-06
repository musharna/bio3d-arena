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
    # the CLIP composition mechanism by returning fruit_only=None.
    fruit_only = (category == "isolated-organ") if n_required >= 2 else None
    return {
        "category": category,
        "score": score,
        "organs_present": parsed["organs_present"],
        "note": parsed["note"],
        "fruit_only": fruit_only,
    }


SPECIES_REP_MIN = 0.5  # probe-tuned (Task 5)


def assess_species_rep(bundle, photo_png: bytes, *, common: str, taxon: str) -> float:
    from .species_id import species_rep_score

    return species_rep_score(bundle, photo_png, common=common, taxon=taxon)


def qa_reference_image(*, organ: dict, species_rep: float | None) -> dict:
    reasons: list[str] = []
    if organ.get("fruit_only"):
        reasons.append("fruit-only / isolated-organ reference")
    if organ.get("category") == "fragment":
        reasons.append("fragment — no expected organ visible")
    if species_rep is not None and species_rep < SPECIES_REP_MIN:
        reasons.append(f"low species-representativeness ({species_rep:.2f} < {SPECIES_REP_MIN})")
    return {"passed": not reasons, "reasons": reasons}
