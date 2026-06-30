"""VLM trait-checking core: forced-tool per-trait verdicts against a rubric.

Pure except for check_traits, which takes an injected Anthropic-like client (built in
scripts/trait_judge.py). Mirrors app.judge. Verdict vocabulary is exactly the four below."""

from __future__ import annotations


from .judge import JUDGE_MODEL

SCORED_CLASSES = {
    "habit",
    "organ_shape",
    "phyllotaxy",
    "inflorescence",
    "color",
    "presence",
    "proportion",
}
VERDICTS = {"present_correct", "present_wrong", "absent", "not_assessable"}

import re  # noqa: E402 — must follow VERDICTS so formatter keeps it with its usages

# Reject patterns derived from the 2026-06-30 judgeability audit. A trait is admissible
# only if it names a static, macroscopic, external, absolute, correctly-attributed,
# concrete morphological feature. Matched against f"{key} {expected}".lower().
_REJECT_EMPTY = {"", "not explicitly stated", "unknown", "n/a", "na", "none", "variable"}
_REJECT_PATTERNS = [
    (
        r"\baccelerat|transition|recurren|\bonset\b|\btiming\b|rate of|maturation|ripening process|"
        r"senescence|degreening|flowering time|earlier flower|delayed flower|bud stage",
        "temporal/process",
    ),
    (
        r"\baltered|\bchange[sd]?\b|reduced|increased|smaller|larger|thicken|prolong|extended|"
        r"superior|affected|malformed|\bdefect|disruption|loss of|abnormal",
        "comparative without baseline",
    ),
    (
        r"trichome|glandular|multicellular|\bcellular|stomat|epiderm|\bmicro|ovary|carpel|"
        r"seed coat|seeds? per|abscission|\bpollen\b|meristem|\brgb\b|brightness|reflectan|quantif",
        "microscopic/internal/instrument",
    ),
    (
        r"\bcommelina|poaceae|\bcannabis|\bflake\b|circularity|lithic|arabidopsis",
        "wrong-taxon/domain token",
    ),
    (
        r"diversif|\bcomplex|architecture|substantial variation|depending on|% of individual|"
        r"across .*(combination|cultivar|hybrid|accession)",
        "vague/population",
    ),
]


def judgeable_reason(trait: dict) -> str | None:
    """Return None if the trait is visually judgeable on a static render of one normal
    specimen, else a short reason string. See the 2026-06-30 morphology-rubrics spec."""
    expected = (trait.get("expected") or "").strip().lower()
    if expected in _REJECT_EMPTY:
        return "no concrete value"
    blob = f"{trait.get('key', '')} {expected}".lower()
    taxon = (trait.get("taxon") or "").lower()
    for pat, reason in _REJECT_PATTERNS:
        m = re.search(pat, blob)
        if m:
            tok = m.group(0).lower()
            # allow an "off-taxon" token only when it actually matches this rubric's taxon
            if reason == "wrong-taxon/domain token" and tok and tok in taxon:
                continue
            return reason
    return None


def is_visually_judgeable(trait: dict) -> bool:
    return judgeable_reason(trait) is None


TRAITS_TOOL = {
    "name": "record_traits",
    "description": "Record, for each listed botanical trait, whether the 3D model satisfies it.",
    "input_schema": {
        "type": "object",
        "properties": {
            "traits": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "trait_key": {"type": "string"},
                        "verdict": {
                            "type": "string",
                            "enum": sorted(VERDICTS),
                            "description": "present_correct=trait present & matches expected; "
                            "present_wrong=present but wrong; absent=missing; "
                            "not_assessable=cannot tell from these views",
                        },
                        "rationale": {"type": "string", "description": "One short phrase."},
                    },
                    "required": ["trait_key", "verdict", "rationale"],
                },
            }
        },
        "required": ["traits"],
    },
}


def _img(b64: str) -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}}


def build_trait_messages(
    species: str, prompt: str, sheet_b64: str, traits: list[dict]
) -> list[dict]:
    lines = "\n".join(
        f"- {t['key']} ({t['trait_class']}): expected {t['expected']}" for t in traits
    )
    text = (
        f"You are checking an AI-generated 3D model of: {species}.\n"
        f"Generation task: {prompt}\n\n"
        "The image is a contact sheet of the model from several angles on a neutral gray "
        "background. For EACH trait below, decide from what is visible whether the model "
        "satisfies it, then call record_traits with one entry per trait (same trait_key). "
        "Use not_assessable only when the views genuinely cannot show the trait.\n\n"
        f"Traits:\n{lines}"
    )
    return [{"role": "user", "content": [{"type": "text", "text": text}, _img(sheet_b64)]}]


def parse_traits(response, traits: list[dict]) -> list[dict]:
    cls_by_key = {t["key"]: t["trait_class"] for t in traits}
    for block in getattr(response, "content", []):
        if (
            getattr(block, "type", None) == "tool_use"
            and getattr(block, "name", "") == "record_traits"
        ):
            rows = (block.input or {}).get("traits", [])
            out = []
            for r in rows:
                key = r.get("trait_key")
                verdict = r.get("verdict")
                if key not in cls_by_key:
                    continue  # ignore keys not in the rubric
                if verdict not in VERDICTS:
                    raise ValueError(f"invalid verdict: {verdict!r}")
                out.append(
                    {
                        "trait_key": key,
                        "trait_class": cls_by_key[key],
                        "verdict": verdict,
                        "rationale": r.get("rationale", ""),
                    }
                )
            return out
    raise ValueError("no record_traits tool_use block in response")


def check_traits(
    client, *, species: str, prompt: str, sheet_b64: str, traits: list[dict]
) -> list[dict]:
    resp = client.messages.create(
        model=JUDGE_MODEL,
        max_tokens=1500,
        tools=[TRAITS_TOOL],
        tool_choice={"type": "tool", "name": "record_traits"},
        messages=build_trait_messages(species, prompt, sheet_b64, traits),
    )
    return parse_traits(resp, traits)
