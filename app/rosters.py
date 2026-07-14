# app/rosters.py
"""Which LLMs compete in the code-gen paradigms.

One module, because the two rosters are NOT interchangeable and the difference is easy to get
wrong: the agentic paradigm shows the model a RENDER of its own mesh and asks it to critique it,
so an agentic entrant must accept image input. A text-only model fails that call — and the harness
records a failed attempt against the model, which /procedural turns into pass@1. A mis-rostered
model would be published as a model that cannot build a mushroom. The roster is scoring input, so
it is tested (tests/test_rosters.py), not trusted.

Procedural has no such constraint: one text prompt in, one bpy script out. That is why the
procedural roster is a superset — it can field the strong TEXT-ONLY reasoners (DeepSeek V4 Pro,
GLM 5.2) that the agentic loop structurally cannot use.

RECENCY IS PART OF THE JOB. A benchmark that quietly measures last season's checkpoints is a
benchmark nobody should trust. The first pass of this roster shipped gpt-5.1 four days after
gpt-5.6 landed, and grok-4.20 three months after grok-4.5. Every id below was checked against the
live OpenRouter catalogue by RELEASE DATE, not by name recall. Re-check before any big commission.

Older versions are KEPT alongside their successors on purpose: gpt-5.1 vs gpt-5.6-sol and
grok-4.20 vs grok-4.5 are version-over-version results, which is a finding, not clutter.
"""

from __future__ import annotations

# Vision-capable. Every id advertises "image" in architecture.input_modalities on OpenRouter.
AGENTIC_ROSTER: list[str] = [
    # --- incumbents (already have results) ---
    "anthropic/claude-opus-4.8",
    "google/gemini-3.1-pro-preview",
    "openai/gpt-5.1",
    "x-ai/grok-4.20",
    "qwen/qwen3.6-plus",
    "moonshotai/kimi-k2.7-code",
    "z-ai/glm-4.6v",
    "meta-llama/llama-4-maverick",
    # --- current flagships (2026-07) ---
    "openai/gpt-5.6-sol",  # the GPT-5.6 flagship tier; luna is the cheap tier, terra the middle
    "openai/gpt-5.6-sol-pro",  # SAME underlying model as sol, higher effort tier -> see app/variants.py
    "x-ai/grok-4.5",
    "qwen/qwen3.7-plus",
    "anthropic/claude-sonnet-5",
    # --- labs the first roster missed entirely ---
    "minimax/minimax-m3",
    "mistralai/mistral-medium-3-5",
]

# The agentic entrants plus the strong TEXT-ONLY reasoners, which procedural can field and agentic
# structurally cannot (no image input -> the critique step is impossible).
PROCEDURAL_ROSTER: list[str] = [
    *AGENTIC_ROSTER,
    "deepseek/deepseek-v3.2",
    "deepseek/deepseek-v4-pro",
    "z-ai/glm-5.2",
]

# Text-only: no image input, so these can never enter the agentic loop. Explicit, so the
# eligibility check is a fact rather than a guess.
TEXT_ONLY = frozenset(
    {
        "deepseek/deepseek-v3.2",
        "deepseek/deepseek-v4-pro",
        "z-ai/glm-5.2",  # GLM 5.2 is text-only; z-ai's vision model is glm-5v-turbo
    }
)


def is_agentic_eligible(model_id: str) -> bool:
    """An agentic entrant must accept an image: the critique step feeds it a render of its own mesh."""
    return model_id not in TEXT_ONLY
