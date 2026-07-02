# tests/test_completeness_scorer.py
import pytest

from app.completeness import score_completeness
from app.organ_inventory import inventory_for

INV = inventory_for("Pinus sylvestris")


class _Block:
    def __init__(self, name, inp):
        self.type = "tool_use"
        self.name = name
        self.input = inp


class _Resp:
    def __init__(self, content):
        self.content = content


class _FakeClient:
    """Mimics anthropic client.messages.create -> response with .content blocks."""

    def __init__(self, resp):
        self._resp = resp
        self.messages = self

    def create(self, **kwargs):
        return self._resp


def test_parses_record_completeness_block():
    payload = {
        "organs_present": [
            {"key": "vegetative_axis", "status": "present"},
            {"key": "foliage", "status": "present"},
            {"key": "reproductive_cone", "status": "absent"},
        ],
        "note": "young pine, no cones",
    }
    client = _FakeClient(_Resp([_Block("record_completeness", payload)]))
    out = score_completeness(client, b"\x89PNG_fake", inventory=INV)
    assert out["organs_present"][0]["key"] == "vegetative_axis"
    assert out["note"] == "young pine, no cones"


def test_raises_when_no_tool_block():
    client = _FakeClient(_Resp([]))
    with pytest.raises(ValueError):
        score_completeness(client, b"\x89PNG_fake", inventory=INV)
