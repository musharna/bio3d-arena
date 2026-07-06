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


def _photo_messages(png: bytes, inventory: TaxonInventory) -> list[dict]:
    import base64

    lines = "\n".join(f"- {o.key}: {o.visual}" for o in inventory.organs)
    b64 = base64.b64encode(png).decode("ascii")
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
                    "source": {"type": "base64", "media_type": "image/png", "data": b64},
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
