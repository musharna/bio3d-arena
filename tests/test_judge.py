from __future__ import annotations

import pytest

from app import judge


class _Block:
    def __init__(self, winner, rationale):
        self.type = "tool_use"
        self.name = "record_verdict"
        self.input = {"winner": winner, "rationale": rationale}


class _Resp:
    def __init__(self, winner, rationale="because"):
        self.content = [_Block(winner, rationale)]


class _FakeClient:
    def __init__(self, winner):
        self._winner = winner
        self.last_kwargs = None

    class _Messages:
        def __init__(self, outer):
            self._outer = outer

        def create(self, **kwargs):
            self._outer.last_kwargs = kwargs
            return _Resp(self._outer._winner)

    @property
    def messages(self):
        return _FakeClient._Messages(self)


def test_swap_group_is_order_independent():
    g1 = judge.swap_group_id(1, 10, 20, 3, "multi4")
    g2 = judge.swap_group_id(1, 20, 10, 3, "multi4")
    assert g1 == g2
    assert judge.swap_group_id(1, 10, 20, 3, "single") != g1


def test_build_messages_has_rubric_and_two_images():
    msgs = judge.build_messages(
        "Tomato",
        "Generate a tomato plant",
        "Visual quality",
        "Mesh cleanliness",
        "QQ==",
        "QQ==",
    )
    assert len(msgs) == 1 and msgs[0]["role"] == "user"
    parts = msgs[0]["content"]
    text = " ".join(p.get("text", "") for p in parts if p["type"] == "text")
    assert "Visual quality" in text and "Mesh cleanliness" in text
    assert "Tomato" in text
    images = [p for p in parts if p["type"] == "image"]
    assert len(images) == 2


def test_parse_verdict_accepts_valid_winner():
    assert judge.parse_verdict(_Resp("a")) == ("a", "because")
    assert judge.parse_verdict(_Resp("tie"))[0] == "tie"


def test_parse_verdict_rejects_garbage():
    with pytest.raises(ValueError):
        judge.parse_verdict(_Resp("left"))


def test_judge_pair_forces_tool_and_returns_winner():
    client = _FakeClient("b")
    winner, rationale = judge.judge_pair(
        client,
        species="Pine",
        prompt="p",
        criterion_name="Overall",
        criterion_desc="best overall",
        sheet_a_b64="QQ==",
        sheet_b_b64="QQ==",
    )
    assert winner == "b"
    assert client.last_kwargs["model"] == judge.JUDGE_MODEL
    assert client.last_kwargs["tool_choice"]["name"] == "record_verdict"
