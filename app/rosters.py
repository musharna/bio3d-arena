# app/rosters.py
"""Which LLMs compete in the code-gen paradigms.

One module, because the two rosters are NOT interchangeable and the difference is easy to get
wrong: the agentic paradigm shows the model a RENDER of its own mesh and asks it to critique it,
so an agentic entrant must accept image input. A text-only model silently fails that loop (or
errors on the image block) — and a failed attempt is recorded against the model, so a mis-rostered
model would look like a model that cannot build a mushroom.

Procedural has no such constraint: it is one text prompt in, one bpy script out. That is why the
procedural roster is a superset — it can field the strong text-only coders (DeepSeek) that the
agentic loop structurally cannot use.

Diversity is the point (task #78): eight labs, not eight re-hosts of two.
"""

from __future__ import annotations

# Vision-capable. Verified against the OpenRouter /models catalogue: every id below advertises
# "image" in architecture.input_modalities. Adding a text-only id here is a bug — see is_agentic_eligible.
AGENTIC_ROSTER: list[str] = [
    "anthropic/claude-opus-4.8",
    "google/gemini-3.1-pro-preview",
    "openai/gpt-5.1",
    "x-ai/grok-4.20",
    "qwen/qwen3.6-plus",
    "moonshotai/kimi-k2.7-code",
    "z-ai/glm-4.6v",
    "meta-llama/llama-4-maverick",
]

# The agentic eight plus the strong TEXT-ONLY coders, which procedural can field and agentic cannot.
PROCEDURAL_ROSTER: list[str] = [
    *AGENTIC_ROSTER,
    "deepseek/deepseek-v3.2",
]

# Models known to be text-only — kept explicit so the eligibility check is a fact, not a guess.
TEXT_ONLY = frozenset({"deepseek/deepseek-v3.2"})


def is_agentic_eligible(model_id: str) -> bool:
    """An agentic entrant must accept an image: the critique step feeds it a render of its own mesh."""
    return model_id not in TEXT_ONLY
