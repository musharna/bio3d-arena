# tests/test_reference_qa.py
from app import reference_qa
from app.organ_inventory import inventory_for


class _Block:
    type = "tool_use"
    name = "record_completeness"

    def __init__(self, inp):
        self.input = inp


class _Resp:
    def __init__(self, inp):
        self.content = [_Block(inp)]


class _FakeClient:
    def __init__(self, inp):
        self._r = _Resp(inp)
        self.messages = self

    def create(self, **kw):
        return self._r


def test_fruit_only_reference_is_flagged():
    inv = inventory_for("Cucurbita pepo")
    assert inv is not None, "Cucurbita pepo inventory must exist"
    # VLM sees ONLY the fruit organ present -> present_count==1 -> 'isolated-organ' -> fruit_only
    fruit_key = next(o.key for o in inv.organs if not o.required)  # a non-vegetative organ
    present = [
        {"key": o.key, "status": ("present" if o.key == fruit_key else "absent")}
        for o in inv.organs
    ]
    client = _FakeClient({"organs_present": present, "note": "only the gourd fruit visible"})
    res = reference_qa.assess_organ_coverage(client, b"\x89PNG", inventory=inv)
    assert res["fruit_only"] is True
    assert res["category"] == "isolated-organ"


def test_whole_plant_reference_not_flagged():
    inv = inventory_for("Solanum lycopersicum")
    present = [{"key": o.key, "status": "present"} for o in inv.organs]
    client = _FakeClient({"organs_present": present, "note": "whole plant"})
    res = reference_qa.assess_organ_coverage(client, b"\x89PNG", inventory=inv)
    assert res["fruit_only"] is False
