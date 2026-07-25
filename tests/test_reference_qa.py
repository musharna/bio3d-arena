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
    # Fungi are _body_inv (the fruiting body is the SOLE required organ). Organ-coverage cannot
    # distinguish body-only from complete -> fruit_only must be None (deferred), not False.
    inv = inventory_for("Boletus edulis")
    assert inv is not None
    body_key = next(o.key for o in inv.organs if o.required)  # the sole required body organ
    present = [
        {"key": o.key, "status": ("present" if o.key == body_key else "absent")} for o in inv.organs
    ]
    client = _FakeClient({"organs_present": present, "note": "a whole fungal fruiting body"})
    res = reference_qa.assess_organ_coverage(client, b"\x89PNG", inventory=inv)
    assert res["fruit_only"] is None


def test_whole_plant_reference_not_flagged():
    inv = inventory_for("Solanum lycopersicum")
    present = [{"key": o.key, "status": "present"} for o in inv.organs]
    client = _FakeClient({"organs_present": present, "note": "whole plant"})
    res = reference_qa.assess_organ_coverage(client, b"\x89PNG", inventory=inv)
    assert res["fruit_only"] is False


def test_qa_combiner_fails_fruit_only():
    r = reference_qa.qa_reference_image(organ={"fruit_only": True, "category": "isolated-organ"})
    assert r["passed"] is False and any("fruit" in x for x in r["reasons"])


def test_qa_combiner_fails_species_mismatch():
    r = reference_qa.qa_reference_image(
        organ={"fruit_only": False, "category": "complete"},
        species={"ok": False, "top": "Zea mays"},
    )
    assert r["passed"] is False and any("species mismatch" in x for x in r["reasons"])


def test_qa_combiner_fails_isolated_composition():
    r = reference_qa.qa_reference_image(
        organ={"fruit_only": None, "category": "complete"},  # body-plan: organ can't tell
        composition={"isolated": True, "note": "lone gourd on a table"},
    )
    assert r["passed"] is False and any("isolated part" in x for x in r["reasons"])


def test_qa_combiner_passes_good():
    r = reference_qa.qa_reference_image(
        organ={"fruit_only": False, "category": "complete"},
        composition={"isolated": False},
        species={"ok": True, "top": "Solanum lycopersicum"},
    )
    assert r["passed"] is True and r["reasons"] == []


def test_species_matches_flags_mismatch(monkeypatch):
    # BioCLIP top-1 is Zea mays but the claim is tomato -> mismatch.
    monkeypatch.setattr(
        "app.species_id.classify_species",
        lambda bundle, png, panel, **kw: {
            "top": "Zea mays",
            "prob": 0.9,
            "margin": 0.8,
            "ranked": [("Zea mays", 0.9)],
        },
    )
    r = reference_qa.species_matches(
        object(),
        b"x",
        claimed_taxon="Solanum lycopersicum",
        panel=["Solanum lycopersicum", "Zea mays"],
    )
    assert r["ok"] is False and r["top"] == "Zea mays"


def test_species_matches_ok_when_top_is_claimed(monkeypatch):
    monkeypatch.setattr(
        "app.species_id.classify_species",
        lambda bundle, png, panel, **kw: {
            "top": "Solanum lycopersicum",
            "prob": 0.9,
            "margin": 0.8,
            "ranked": [],
        },
    )
    r = reference_qa.species_matches(
        object(), b"x", claimed_taxon="Solanum lycopersicum", panel=["Zea mays"]
    )
    assert r["ok"] is True


def test_assess_composition_parses_isolated():
    class _B:
        type = "tool_use"
        input = {"shows": "isolated_part", "note": "just a picked gourd"}

    class _R:
        content = [_B()]

    class _C:
        messages = property(lambda self: self)

        def create(self, **kw):
            return _R()

    res = reference_qa.assess_composition(
        _C(), b"\xff\xd8\xff jpeg", taxon="Cucurbita pepo", common="gourd"
    )
    assert res["isolated"] is True and "gourd" in res["note"]


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
