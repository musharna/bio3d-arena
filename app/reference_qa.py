"""Quality-assessment for reference images. Reuses the completeness VLM machinery: a reference
photo of a single organ (e.g. a lone tomato fruit) maps to `derive`'s 'isolated-organ' category
== fruit_only. `fruit_only` is `bool | None`: for a single-required-organ body plan (fungi,
gourd — the fruit/body IS the whole organism) organ-coverage cannot distinguish fruit-only from
complete, so it is `None` (undeterminable; deferred to the CLIP composition mechanism). Uses a
PHOTO-framed prompt, not the 3D-render-sheet framing."""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import unquote

from .completeness import COMPLETENESS_TOOL, _parse, derive
from .judge import JUDGE_MODEL
from .organ_inventory import TaxonInventory

# --- reference independence -------------------------------------------------------------
# A reference photo must not be the photo a reconstruction was generated FROM: a recon that
# reproduces its own input reads as faithful even when it is biologically wrong.
#
# `service.reference_images_for_task` already refuses to show a task's `meta_json.input_image`.
# That guard suppresses one ASSET PATH, and the gallery is sourced independently from
# iNaturalist — so the same underlying photo can arrive as a different file, at a different
# rendition, and be shown anyway. Measured 2026-08-08: `zea_mays/4.jpg`,
# `morchella_esculenta/2.jpg` and `trametes_versicolor/1.jpg` were each the recon input for
# 10-11 outputs (tasks 12/26/27) and all three were passing QA and being served.
#
# Comparing bytes does NOT catch it — the input is `/photos/<id>/original.jpg` while the gallery
# holds `/photos/<id>/medium.jpg`. Identity has to be the SOURCE PHOTO, not the file.

_INAT_PHOTO = re.compile(r"/photos/(\d+)")
# Commons serves both `/commons/a/ab/File.jpg` and `/commons/thumb/a/ab/File.jpg/800px-File.jpg`;
# the segment after the two hash dirs is the canonical file name in both.
_WIKIMEDIA_FILE = re.compile(r"/commons/(?:thumb/)?[0-9a-f]/[0-9a-f]{2}/([^/]+)")


def photo_identity(url: str | None) -> str | None:
    """Identity of the SOURCE photo behind `url`, stable across renditions and thumbnail sizes.

    Namespaced by host so a numeric iNaturalist id can never collide with a Commons file name.
    Returns None for anything unrecognised — callers must treat that as "no opinion", never as
    a match, or an unparsed URL would silently reject a legitimate reference.
    """
    if not url:
        return None
    m = _INAT_PHOTO.search(url)
    if m:
        return f"inat:{m.group(1)}"
    m = _WIKIMEDIA_FILE.search(url)
    if m:
        return f"wikimedia:{unquote(m.group(1))}"
    return None


def recon_input_photo_ids(reference_dir: Path) -> set[str]:
    """Source-photo identities of every recon INPUT image, read from the `*_ref*.json` sidecars.

    Deliberately reads the sidecars on disk rather than the DB: this runs inside gallery QA,
    which is read-only w.r.t. the database, and every `input_image` recorded on a model output
    is a `reference/<file>` that has one of these sidecars. Superseded inputs (`*_old.json`)
    are included on purpose — excluding a photo that ONCE fed a generator costs one reference
    image; including it risks a circular one.
    """
    ids: set[str] = set()
    for sidecar in sorted(Path(reference_dir).glob("*_ref*.json")):
        try:
            meta = json.loads(sidecar.read_text())
        except (ValueError, OSError):
            continue
        if not isinstance(meta, dict):
            continue
        for key in ("download_url", "source_url", "url"):
            ident = photo_identity(meta.get(key))
            if ident:
                ids.add(ident)
                break
    return ids


def assess_independence(
    *,
    photo_id: int | str | None = None,
    url: str | None = None,
    input_photo_ids: set[str] | None = None,
) -> dict:
    """Is this gallery photo independent of the recon inputs? `ok=False` means it IS an input.

    Checks the manifest's `photo_id` as well as the URL: manifests carry the id directly, and an
    entry whose url is missing or in an unrecognised form must not slip through unchecked.
    With an empty input set the predicate ABSTAINS (`ok=True`) rather than failing closed — a
    caller that could not load the inputs would otherwise reject every reference image, which is
    the failure mode that hid the whole gallery in 2026-08-05.
    """
    known = input_photo_ids or set()
    if not known:
        return {"ok": True, "identity": None, "abstained": True}

    candidates = {photo_identity(url)}
    if photo_id is not None and str(photo_id).strip():
        candidates.add(f"inat:{photo_id}")
    hit = next((c for c in candidates if c and c in known), None)
    return {"ok": hit is None, "identity": hit, "abstained": False}


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


def composition_applies(inventory: TaxonInventory | None) -> bool:
    """Is `assess_composition` meaningful for this taxon?

    Its prompt is written entirely in plant/fungus terms — "a plant with stems/leaves/roots, or
    a whole intact fungus on its substrate" against "a single picked fruit/gourd sitting on a
    table or a lone cut mushroom" — because it predates animals as a third kingdom. Applied to
    an animal it misfires: on 2026-07-29 it rejected 6 of 8 surviving dog photos as "isolated
    part", including a clean head-and-shoulders portrait of a poodle in a meadow, which is not a
    detached part of anything.

    Animals are detected by a PAIRED part — an organ whose expected complement exceeds 1 (dog
    legs x4, duck wings x2, monarch legs x6, goldfish pectoral fins x2). `Organ.complement`
    defaults to 1, so plants and fungi never trip it; `_animal_inv` is the only constructor that
    passes anything else. This is indirect — `TaxonInventory` carries no kingdom — so the test
    pins all four animal taxa explicitly, and a future animal whose parts were all singular
    would need one. For animals `assess_subject` already judges usability via its
    `not_identifiable` verdict, so the fruit-only question is simply not applicable."""
    if inventory is None:
        return False
    return not any(o.complement > 1 for o in inventory.organs)


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
#
# KEYS ARE ORGAN_INVENTORY TAXON NAMES — that is what a gallery slug resolves to, so any other
# spelling is unreachable dead code (see test_every_morphotype_is_reachable_by_the_gate). The dog
# was keyed on iNaturalist's 'Canis familiaris' while the gate passes the arena's trinomial, so
# the dingo exclusion below never once reached the model; a stale pumpkin entry sat beside it,
# left behind when Cucurbita pepo was removed from the corpus.
#
# A morphotype must describe WHAT THE TASK ASKS FOR and no more. The goldfish entry first added
# "in water", a photographic condition task 31 never asks for, and threw away two specimens whose
# whole lateral profile and fins were perfectly legible. Over-strict costs references as surely as
# absent admits bad ones.
MORPHOTYPE = {
    "Rosa": "a cultivated garden rose in bloom, many overlapping petals — NOT a wild five-petalled dog-rose or a bramble",
    "Canis lupus familiaris": "a typical domestic dog breed — NOT a dingo, dhole, coyote or other wild canid",
    "Hericium erinaceus": "lion's mane: a single unbranched cushion of long downward-hanging spines — NOT the branched, coral-like H. coralloides or H. americanum",
    "Trametes versicolor": "turkey tail: thin concentrically-banded brackets in overlapping tiers",
    "Carassius auratus": "a whole goldfish showing the full body with its fins — NOT one crushed in a predator's beak or with the body hidden by a hand",
    # Task 30 asks for "a whole monarch butterfly". Left to the bare common name the verdict rested
    # on the model's own reading of whether a caterpillar counts as a butterfly; iNaturalist's pool
    # holds 9,259 CC research-grade larval records, so that reading is load-bearing.
    "Danaus plexippus": "an adult monarch butterfly with its wings — NOT the striped caterpillar (larva) or the green chrysalis (pupa)",
    # Added 2026-08-04 after a by-eye sweep of all 16 galleries. The entries above already named,
    # correctly, most of what that sweep found — the bramble, the dingo, the fish in a beak, the
    # larva and pupa — but the scorer had never been run, so none of it was ever applied. These
    # five cover the failures no entry could have caught because none existed.
    #
    # They are deliberately about SUBJECT LEGIBILITY rather than taxonomy: every photo below is
    # the right species. What made them useless as a reference is that the organism is incidental
    # to the frame, or is not the growth stage the task names. Per the note above, each states
    # only what its task asks for — an unusual habitat or a wild-growing specimen is legitimate
    # variation and must keep passing.
    "Solanum lycopersicum": "a tomato plant legible as the subject of the frame, with its foliage — NOT a distant view in which the plant is incidental to a wall, drain or waterway",
    "Pinus sylvestris": "a Scots pine legible as a whole tree — NOT a bark or trunk detail alone, and NOT a distant landscape in which the tree is a few pixels",
    "Boletus edulis": "one or a few porcini legible as individual fruiting bodies — NOT a long row of harvested specimens laid out for a haul photograph",
    "Glycine max": "a soybean plant, or its pods or flowers in close-up — NOT a whole-field view in which no individual plant is legible",
    # Task 10 asks specifically for the "whole-plant rosette", so a bolted plant photographed for
    # its flowering stem is the wrong stage even though it is the right species.
    "Arabidopsis thaliana": "a thale cress with its basal leaf rosette legible — a flowering stem may be present, but NOT a photograph of the inflorescence alone",
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
    independence: dict | None = None,
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
    if independence is not None and not independence.get("ok", True):
        reasons.append(
            f"this photo IS a reconstruction input ({independence.get('identity')}) — "
            "showing it as the reference is circular"
        )
    return {"passed": not reasons, "reasons": reasons}
