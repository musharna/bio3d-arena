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
