from __future__ import annotations

import base64
import io
import os

import pytest

from app import judge


def _png_b64() -> str:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (64, 64), "green").save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="no ANTHROPIC_API_KEY")
def test_one_live_vision_call_returns_valid_winner():
    """Real-execution check: a single live Claude vision call end-to-end."""
    import anthropic

    client = anthropic.Anthropic()
    b64 = _png_b64()
    winner, rationale = judge.judge_pair(
        client,
        species="Tomato",
        prompt="Generate a tomato plant",
        criterion_name="Overall",
        criterion_desc="best output overall",
        sheet_a_b64=b64,
        sheet_b_b64=b64,
    )
    assert winner in {"a", "b", "tie", "bad"}
    assert isinstance(rationale, str)
