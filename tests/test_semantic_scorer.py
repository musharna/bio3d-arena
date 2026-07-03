# tests/test_semantic_scorer.py
import pytest

from app.semantic import (
    REJECT_CODES,
    _build_messages,
    score_semantic,
    verdict_from_code,
)


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


def test_reject_codes_map_to_non_admit_with_reason():
    for code in ["multiple", "sub_part", "not_a_plant", "wrong_species"]:
        v = verdict_from_code(code, "because")
        assert v.admit is False
        assert v.reason == code
        assert v.detail["code"] == code and v.detail["note"] == "because"


def test_ok_and_uncertain_admit():
    assert verdict_from_code("ok").admit is True
    assert verdict_from_code("uncertain").admit is True


def test_unrecognized_code_admits_precision_first():
    v = verdict_from_code("banana")
    assert v.admit is True and v.reason == ""


def test_reject_codes_constant_is_the_four_semantic_failures():
    assert REJECT_CODES == {"multiple", "sub_part", "not_a_plant", "wrong_species"}


def test_build_messages_taxon_present_includes_wrong_species_clause():
    msgs = _build_messages(b"\x89PNG", taxon="tomato")
    text = msgs[0]["content"][0]["text"]
    assert "tomato" in text and "wrong_species" in text


def test_build_messages_taxon_none_omits_wrong_species():
    msgs = _build_messages(b"\x89PNG", taxon=None)
    text = msgs[0]["content"][0]["text"]
    assert "wrong_species" not in text
    # still has the taxon-agnostic reject codes + an image block
    assert "multiple" in text and "not_a_plant" in text
    assert msgs[0]["content"][1]["type"] == "image"


def test_score_semantic_parses_tool_block():
    client = _FakeClient(
        _Resp([_Block("record_admissibility", {"verdict": "multiple", "note": "two plants"})])
    )
    out = score_semantic(client, b"\x89PNG", taxon="maize")
    assert out == {"verdict": "multiple", "note": "two plants"}


def test_score_semantic_raises_without_tool_block():
    client = _FakeClient(_Resp([]))
    with pytest.raises(ValueError):
        score_semantic(client, b"\x89PNG", taxon=None)
