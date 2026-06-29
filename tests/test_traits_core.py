from __future__ import annotations

from app import traits


class _Block:
    type = "tool_use"
    name = "record_traits"

    def __init__(self, data):
        self.input = data


class _Resp:
    def __init__(self, data):
        self.content = [_Block(data)]


class _Client:
    def __init__(self, data):
        self._data = data
        self.messages = self

    def create(self, **kw):
        return _Resp(self._data)


def test_check_traits_parses_per_trait_verdicts():
    rubric = [
        {"key": "spadix", "trait_class": "presence", "expected": "present"},
        {"key": "leaf_shape", "trait_class": "organ_shape", "expected": "ovate"},
    ]
    client = _Client(
        {
            "traits": [
                {"trait_key": "spadix", "verdict": "present_correct", "rationale": "visible"},
                {"trait_key": "leaf_shape", "verdict": "absent", "rationale": "no leaves"},
            ]
        }
    )
    out = traits.check_traits(
        client, species="Amorphophallus", prompt="model it", sheet_b64="x", traits=rubric
    )
    by = {o["trait_key"]: o for o in out}
    assert by["spadix"]["verdict"] == "present_correct"
    assert by["spadix"]["trait_class"] == "presence"  # carried from the rubric
    assert by["leaf_shape"]["verdict"] == "absent"


def test_parse_traits_rejects_unknown_verdict():
    rubric = [{"key": "k", "trait_class": "color", "expected": "red"}]
    bad = _Resp({"traits": [{"trait_key": "k", "verdict": "banana", "rationale": ""}]})
    try:
        traits.parse_traits(bad, rubric)
        assert False, "expected ValueError"
    except ValueError:
        pass
