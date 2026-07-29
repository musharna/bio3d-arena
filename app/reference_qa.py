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


# What the reference SHOULD depict, where the bare binomial is not enough to pin it down.
# Only taxa whose galleries were measurably wrong on 2026-07-29 are listed; everything else
# falls back to "<common> (<taxon>)", which is sufficient when the name is unambiguous.
#
# Two distinct causes are corrected here. `Rosa` is a genus whose iNaturalist text search
# resolves to the ORDER Rosales (brambles, elms, figs), and the arena's rose tasks ask for
# full-petalled garden roses, not wild dog-roses. The three `complex` taxa resolve to a
# species-complex — by definition a group too similar to separate — so their curated photos
# legitimately include sibling species (lion's mane came back 8/8 as coral tooth and bear's
# head). Naming the intended form lets the subject check reject siblings the taxonomy accepts.
MORPHOTYPE = {
    "Rosa": "a cultivated garden rose in bloom, many overlapping petals — NOT a wild five-petalled dog-rose or a bramble",
    "Canis familiaris": "a typical domestic dog breed — NOT a dingo, dhole, coyote or other wild canid",
    "Hericium erinaceus": "lion's mane: a single unbranched cushion of long downward-hanging spines — NOT the branched, coral-like H. coralloides or H. americanum",
    "Cucurbita pepo": "a whole pumpkin or squash fruit on the plant",
    "Trametes versicolor": "turkey tail: thin concentrically-banded brackets in overlapping tiers",
    "Carassius auratus": "a live goldfish, whole and unobstructed, in water",
}


def morphotype_for(taxon: str) -> str:
    """The intended form for `taxon`, or "" to let `assess_subject` fall back to the name."""
    return MORPHOTYPE.get(taxon, "")


SUBJECT_TOOL = {
    "name": "record_subject",
    "description": "Record what a reference photo's MAIN SUBJECT actually is.",
    "input_schema": {
        "type": "object",
        "properties": {
            "subject": {
                "type": "string",
                "description": "The main subject actually shown (scientific name if confident).",
            },
            "verdict": {
                "type": "string",
                "enum": ["match", "different_organism", "wrong_form", "not_identifiable"],
                "description": (
                    "match = the claimed organism, in the expected form, as the main subject. "
                    "different_organism = something else dominates the frame. "
                    "wrong_form = the right taxon but the wrong morphotype. "
                    "not_identifiable = too obscured/distant/damaged to judge a 3D model against."
                ),
            },
            "note": {"type": "string"},
        },
        "required": ["subject", "verdict", "note"],
    },
}

_SUBJECT_OK = "match"


def assess_subject(
    client, photo_png: bytes, *, taxon: str, common: str, morphotype: str = ""
) -> dict:
    """Is the claimed organism THIS photo's main subject, in the form the task expects?

    Sourcing selects reference photos for taxonomic correctness — iNaturalist guarantees a
    research-grade record is a true record of the taxon, and guarantees nothing about whether
    the photo is a usable visual reference. Those are different properties, and only the first
    was ever checked. This closes that gap, and deliberately asks a question `species_matches`
    cannot express:

    * a heron with a goldfish in its beak is a valid *Carassius auratus* record whose main
      subject is a heron -> different_organism;
    * a dingo is a valid *Canis familiaris* record but not the domestic dog the generators
      produce, and a wild dog-rose is a valid *Rosa* record but not a garden rose ->
      wrong_form, which no taxonomic test can catch because the taxon IS right.

    VLM-based on purpose: `species_matches` routes through BioCLIP (open_clip/torch), which is
    absent from the runtime deps, so the species half of gallery QA silently never ran on hosts
    without them. Returns {"ok", "subject", "verdict", "note"}.
    """
    import base64

    want = morphotype or f"{common} ({taxon})"
    text = (
        f"This photograph is used as a REFERENCE for {common} ({taxon}) — a voter looks at it to "
        f"judge whether a 3D model resembles the real organism. The reference should show "
        f"{want}.\n\n"
        "Judge the MAIN SUBJECT of the frame, not merely what is present in it. Reject the photo "
        "if something else dominates (a predator holding it, the habitat, a person), if it is the "
        "right taxon in the wrong form (a wild ancestral type where a cultivated or domestic one "
        "is wanted, or vice versa), or if it is too obscured, distant, dead or damaged to judge a "
        "3D model against. A correct identification is NOT sufficient — an accurate record of the "
        "species can still be a useless reference. Then call record_subject."
    )
    resp = client.messages.create(
        model=JUDGE_MODEL,
        max_tokens=400,
        tools=[SUBJECT_TOOL],
        tool_choice={"type": "tool", "name": "record_subject"},
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": _sniff_media_type(photo_png),
                            "data": base64.b64encode(photo_png).decode("ascii"),
                        },
                    },
                    {"type": "text", "text": text},
                ],
            }
        ],
    )
    block = next(b for b in resp.content if getattr(b, "type", None) == "tool_use")
    verdict = block.input.get("verdict", "not_identifiable")
    return {
        "ok": verdict == _SUBJECT_OK,
        "subject": block.input.get("subject", ""),
        "verdict": verdict,
        "note": block.input.get("note", ""),
    }


def qa_reference_image(
    *,
    organ: dict | None = None,
    composition: dict | None = None,
    species: dict | None = None,
    subject: dict | None = None,
) -> dict:
    """Combine QA signals into a pass/fail verdict. `composition` = assess_composition output
    (isolated=True → a detached/harvested part, not the whole organism) is the calibrated
    fruit-only signal for ALL taxa; `species` = species_matches output (mismatch via ok=False).
    `organ` = assess_organ_coverage output is OPTIONAL and NOT used for reference QA: its
    'isolated-organ' category over-fires on legitimate single-organ views (e.g. an iconic
    Arabidopsis rosette with the bolt out of frame reads as isolated-organ but is a fine
    reference). It is accepted only for callers that explicitly want the organ-coverage signal;
    the gallery QA passes composition+species. Any triggered signal fails the image."""
    reasons: list[str] = []
    if organ is not None and organ.get("fruit_only"):
        reasons.append("fruit-only / isolated-organ reference (organ-coverage)")
    if organ is not None and organ.get("category") == "fragment":
        reasons.append("fragment — no expected organ visible")
    if composition is not None and composition.get("isolated"):
        reasons.append("isolated part, not the whole organism (VLM composition)")
    if species is not None and not species.get("ok", True):
        reasons.append(f"species mismatch — reads as {species.get('top')!r}, not the claimed taxon")
    if subject is not None and not subject.get("ok", True):
        reasons.append(
            f"subject is {subject.get('subject')!r} ({subject.get('verdict')}), "
            "not the claimed organism in the expected form"
        )
    return {"passed": not reasons, "reasons": reasons}
