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


from app import reference_qa as rq


def test_qa_combiner_fails_fruit_only():
    r = rq.qa_reference_image(
        organ={"fruit_only": True, "category": "isolated-organ"}, species_rep=0.9
    )
    assert r["passed"] is False and any("fruit" in x for x in r["reasons"])


def test_qa_combiner_fails_low_species_rep():
    r = rq.qa_reference_image(organ={"fruit_only": False, "category": "complete"}, species_rep=0.1)
    assert r["passed"] is False and any("species" in x for x in r["reasons"])


def test_qa_combiner_passes_good():
    r = rq.qa_reference_image(organ={"fruit_only": False, "category": "complete"}, species_rep=0.9)
    assert r["passed"] is True and r["reasons"] == []


def test_sniff_media_type_from_magic_bytes():
    assert reference_qa._sniff_media_type(b"\xff\xd8\xff\xe0\x00\x10JFIF") == "image/jpeg"
    assert reference_qa._sniff_media_type(b"\x89PNG\r\n\x1a\n....") == "image/png"
    assert reference_qa._sniff_media_type(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"


def test_photo_messages_declares_jpeg_for_jpeg_bytes():
    # Regression for the Anthropic 400 "media type png but bytes are jpeg" bug: reference photos
    # are JPEG, so the declared media_type must match the actual bytes, not a hardcoded png.
    inv = inventory_for("Solanum lycopersicum")
    msgs = reference_qa._photo_messages(b"\xff\xd8\xff\xe0 jpeg-bytes", inv)
    assert msgs[0]["content"][0]["source"]["media_type"] == "image/jpeg"
