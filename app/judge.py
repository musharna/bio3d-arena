"""VLM-as-judge core: prompt construction, forced-tool verdict parsing, one-pair call.

Pure except for `judge_pair`, which takes an injected Anthropic-like client (the real
client is built in scripts/judge_vlm.py from ANTHROPIC_API_KEY). Winner vocabulary is
exactly {a,b,tie,bad} to match human Vote.winner."""

from __future__ import annotations

import hashlib

JUDGE_MODEL = "claude-sonnet-4-6"
_VALID = {"a", "b", "tie", "bad"}

VERDICT_TOOL = {
    "name": "record_verdict",
    "description": "Record which 3D model better satisfies the criterion.",
    "input_schema": {
        "type": "object",
        "properties": {
            "winner": {
                "type": "string",
                "enum": ["a", "b", "tie", "bad"],
                "description": "a=Model A better, b=Model B better, tie=equal, "
                "bad=both unusable for this criterion",
            },
            "rationale": {"type": "string", "description": "One sentence justification."},
        },
        "required": ["winner", "rationale"],
    },
}


def swap_group_id(
    task_id: int, output_id_x: int, output_id_y: int, criterion_id: int, condition: str
) -> str:
    """Order-independent id for one logical comparison (links the A/B & B/A votes)."""
    lo, hi = sorted((output_id_x, output_id_y))
    raw = f"{task_id}:{lo}:{hi}:{criterion_id}:{condition}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _img(b64: str) -> dict:
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": b64},
    }


def build_messages(
    species: str,
    prompt: str,
    criterion_name: str,
    criterion_desc: str,
    sheet_a_b64: str,
    sheet_b_b64: str,
) -> list[dict]:
    """One user message: rubric + Model A image + Model B image."""
    text = (
        f"You are judging two AI-generated 3D models of: {species}.\n"
        f"Generation task: {prompt}\n\n"
        f"Criterion — {criterion_name}: {criterion_desc}\n\n"
        "Each image is a contact sheet of one model rendered from several angles on a "
        "neutral gray background. The FIRST image is Model A; the SECOND is Model B. "
        "Decide which model better satisfies the criterion, then call record_verdict. "
        "Use 'tie' only if genuinely indistinguishable, and 'bad' only if BOTH are "
        "unusable for this criterion."
    )
    return [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": text},
                {"type": "text", "text": "Model A:"},
                _img(sheet_a_b64),
                {"type": "text", "text": "Model B:"},
                _img(sheet_b_b64),
            ],
        }
    ]


def parse_verdict(response) -> tuple[str, str]:
    """Extract (winner, rationale) from the forced tool_use block."""
    for block in getattr(response, "content", []):
        if (
            getattr(block, "type", None) == "tool_use"
            and getattr(block, "name", "") == "record_verdict"
        ):
            data = block.input or {}
            winner = data.get("winner")
            if winner not in _VALID:
                raise ValueError(f"invalid winner: {winner!r}")
            return winner, data.get("rationale", "")
    raise ValueError("no record_verdict tool_use block in response")


def judge_pair(
    client,
    *,
    species: str,
    prompt: str,
    criterion_name: str,
    criterion_desc: str,
    sheet_a_b64: str,
    sheet_b_b64: str,
) -> tuple[str, str]:
    """Call the VLM with a forced verdict tool; return (winner, rationale)."""
    resp = client.messages.create(
        model=JUDGE_MODEL,
        max_tokens=400,
        tools=[VERDICT_TOOL],
        tool_choice={"type": "tool", "name": "record_verdict"},
        messages=build_messages(
            species, prompt, criterion_name, criterion_desc, sheet_a_b64, sheet_b_b64
        ),
    )
    return parse_verdict(resp)
