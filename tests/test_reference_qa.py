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


def test_fruit_only_plant_reference_is_flagged():
    # Tomato is _inv (2 required: vegetative_axis + foliage). A photo of ONLY the fruit ->
    # required organs absent, present_count==1 -> 'isolated-organ' -> fruit_only True.
    inv = inventory_for("Solanum lycopersicum")
    assert inv is not None
    present = [
        {"key": o.key, "status": ("present" if o.key == "reproductive_fruit" else "absent")}
        for o in inv.organs
    ]
    client = _FakeClient({"organs_present": present, "note": "only a tomato fruit"})
    res = reference_qa.assess_organ_coverage(client, b"\x89PNG", inventory=inv)
    assert res["fruit_only"] is True
    assert res["category"] == "isolated-organ"


def test_body_plan_taxon_defers_fruit_only():
    # Cucurbita/fungi are _body_inv (fruit/body is the SOLE required organ). Organ-coverage
    # cannot distinguish fruit-only from complete -> fruit_only must be None (deferred), not False.
    inv = inventory_for("Cucurbita pepo")
    assert inv is not None
    present = [
        {"key": o.key, "status": ("present" if o.key == "fruit_body" else "absent")}
        for o in inv.organs
    ]
    client = _FakeClient({"organs_present": present, "note": "a whole gourd fruit"})
    res = reference_qa.assess_organ_coverage(client, b"\x89PNG", inventory=inv)
    assert res["fruit_only"] is None


def test_whole_plant_reference_not_flagged():
    inv = inventory_for("Solanum lycopersicum")
    present = [{"key": o.key, "status": "present"} for o in inv.organs]
    client = _FakeClient({"organs_present": present, "note": "whole plant"})
    res = reference_qa.assess_organ_coverage(client, b"\x89PNG", inventory=inv)
    assert res["fruit_only"] is False
