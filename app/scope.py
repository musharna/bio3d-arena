"""Per-model 'scope' — which plant parts a 3D model actually depicts — and the derived
(model, trait) assessability gate.

A single detached tomato fruit is a valid model of a fruit, but you cannot judge habit,
phyllotaxy, leaf shape, or inflorescence on it — those need the whole plant. Grading them
anyway lets the VLM 'see' structures that aren't there and inflates the botanical score.
`scope_judge.py` classifies each model into a ModelScope row (is_plant + visible_parts);
scoring and calibration consult `is_assessable` so a trait is only judged on a model that
actually depicts the structure the trait describes.

Pure functions only — no DB, no network — so they are unit-tested directly."""

from __future__ import annotations

# Vocabulary the scope classifier reports as "clearly visible in the model".
SCOPE_PARTS = ("whole_plant", "foliage", "flower", "fruit", "cone", "stem_or_trunk")

# organ keyword -> scope part, scanned against an organ-specific trait's key/expected/visual.
# Order matters: the first group that matches wins (fruit/cone before the generic stem group).
_ORGAN_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("needle", "leaf", "foliage", "rosette", "blade"), "foliage"),
    (("fruit", "berry", "hip", "pod", "silique"), "fruit"),
    (("cone",), "cone"),
    (("bark", "trunk", "stem", "culm"), "stem_or_trunk"),
    (
        ("flower", "petal", "pigment", "inflorescence", "tassel", "raceme", "corymb", "cyme"),
        "flower",
    ),
]


def required_parts(trait: dict) -> set[str]:
    """Which scope parts must be visible for `trait` to be judgeable.

    - habit / phyllotaxy / proportion → the whole plant (architecture).
    - inflorescence → flowers (or a flower cluster / tassel / spike).
    - presence → nothing special (checkable on any plant view; 'absent' is a valid verdict).
    - organ_shape / color → the specific organ, derived from the trait's key/expected/visual
      keywords (e.g. leaf_form → foliage, fruit_color_ripe → fruit, bark_color → stem_or_trunk).

    Returns an empty set when the required organ can't be determined — fail open, don't exclude.
    """
    cls = trait.get("trait_class", "")
    if cls in ("habit", "phyllotaxy", "proportion"):
        return {"whole_plant"}
    if cls == "inflorescence":
        return {"flower"}
    if cls == "presence":
        return set()
    text = " ".join(str(trait.get(k, "")) for k in ("key", "expected", "visual")).lower()
    for kws, part in _ORGAN_KEYWORDS:
        if any(w in text for w in kws):
            return {part}
    return set()


def is_assessable(scope: dict | None, trait: dict) -> bool:
    """True if `trait` can be judged on a model with this `scope`.

    `scope` is {"is_plant": bool, "visible_parts": [str]} or None. None → True (no scope
    info yet; fail open so the system works before/without a scope pass). A non-plant model →
    nothing assessable. Otherwise every required part must be visible; a visible whole_plant
    implies foliage + stem_or_trunk (a whole plant has a stem and leaves), but NOT flower /
    fruit / cone (those are seasonal/optional and must be reported explicitly)."""
    if scope is None:
        return True
    if not scope.get("is_plant", True):
        return False
    req = required_parts(trait)
    if not req:
        return True
    visible = set(scope.get("visible_parts") or [])
    if "whole_plant" in visible:
        visible |= {"foliage", "stem_or_trunk"}
    return req <= visible


# --------------------------------------------------------------------------- #
# VLM scope classifier (one forced-tool call per model). Pure prompt/parse here #
# so they are unit-tested; scope_judge.py wires the Anthropic client + sheets.  #
# --------------------------------------------------------------------------- #

SCOPE_TOOL = {
    "name": "record_scope",
    "description": "Record which plant parts are clearly visible in the 3D model.",
    "input_schema": {
        "type": "object",
        "properties": {
            "is_plant": {
                "type": "boolean",
                "description": (
                    "true if the model is a recognizable plant or plant part; false if it is "
                    "junk / not a plant / an unrecognizable blob"
                ),
            },
            "visible_parts": {
                "type": "array",
                "items": {"type": "string", "enum": list(SCOPE_PARTS)},
                "description": "the parts CLEARLY visible in the model (report only what is shown)",
            },
            "rationale": {"type": "string", "description": "one short sentence"},
        },
        "required": ["is_plant", "visible_parts", "rationale"],
    },
}


def _img(b64: str) -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}}


def build_scope_messages(species: str, prompt: str, sheet_b64: str) -> list[dict]:
    text = (
        f"You are inspecting an AI-generated 3D model intended to depict: {species}.\n"
        f"Generation task: {prompt}\n\n"
        "The image is a contact sheet of the model from several angles on a neutral gray "
        "background. Report ONLY what the model actually depicts — do NOT assume parts that a "
        "real plant of this species would normally have.\n\n"
        "Set is_plant=false if it is not a recognizable plant or plant part (a junk blob, a "
        "random object, or an unrecognizable mess).\n\n"
        "In visible_parts include each of these that is CLEARLY present:\n"
        "- whole_plant: the overall plant architecture is visible — you can see how the whole "
        "plant is built (stem with leaves arranged on it), not just one detached organ\n"
        "- foliage: leaves or needles\n"
        "- flower: flowers, petals, or a flower cluster / tassel / spike\n"
        "- fruit: a fruit, berry, pod, hip, silique, or seed pod\n"
        "- cone: a conifer cone\n"
        "- stem_or_trunk: a stem, trunk, culm, or bark surface\n\n"
        "Example: a model that is a single tomato fruit → is_plant=true, "
        "visible_parts=[fruit] (NOT whole_plant, NOT foliage)."
    )
    return [{"role": "user", "content": [{"type": "text", "text": text}, _img(sheet_b64)]}]


def parse_scope(response) -> dict:
    """Extract {is_plant, visible_parts, rationale} from the forced record_scope tool_use.
    Unknown parts (outside SCOPE_PARTS) are dropped; parts are deduped + sorted."""
    for block in getattr(response, "content", []):
        if (
            getattr(block, "type", None) == "tool_use"
            and getattr(block, "name", "") == "record_scope"
        ):
            data = block.input or {}
            parts = [p for p in (data.get("visible_parts") or []) if p in SCOPE_PARTS]
            return {
                "is_plant": bool(data.get("is_plant", True)),
                "visible_parts": sorted(set(parts)),
                "rationale": data.get("rationale", ""),
            }
    raise ValueError("no record_scope tool_use block in response")


def classify_scope(client, *, species: str, prompt: str, sheet_b64: str, model: str | None = None):
    from .judge import JUDGE_MODEL

    resp = client.messages.create(
        model=model or JUDGE_MODEL,
        max_tokens=300,
        tools=[SCOPE_TOOL],
        tool_choice={"type": "tool", "name": "record_scope"},
        messages=build_scope_messages(species, prompt, sheet_b64),
    )
    return parse_scope(resp)
