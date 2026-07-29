# tests/test_reference_qa.py

import pytest

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


# --- subject check ---------------------------------------------------------------------
#
# The gap this closes (measured 2026-07-29 over all 130 gallery photos): sourcing selected on
# TAXONOMIC CORRECTNESS and nothing ever checked VISUAL REPRESENTATIVENESS. iNaturalist
# guarantees the first — a research-grade record is a true record of the taxon — and says
# nothing about the second. A great blue heron with a goldfish crushed in its beak is a
# flawless *Carassius auratus* record and a useless reference for judging a 3D goldfish;
# a dingo is a legitimate *Canis familiaris* record and a misleading reference for the
# domestic dogs the generators produce. `species_matches` cannot cover this: it routes
# through BioCLIP, which needs open_clip/torch — deliberately absent from the runtime deps —
# so on any machine without them the species half of gallery QA silently never ran.


class _SubjectClient:
    """Captures the outbound request so the prompt itself can be asserted on."""

    def __init__(self, payload):
        self.payload = payload
        self.seen = None
        self.messages = self

    def create(self, **kw):
        self.seen = kw

        class _B:
            type = "tool_use"
            input = self.payload

        class _R:
            content = [_B()]

        return _R()


def test_subject_check_rejects_a_photo_whose_main_subject_is_another_organism():
    """The heron case. The goldfish IS in frame and the record is valid, so any check that only
    asks 'is the taxon present?' passes it. The question has to be about the SUBJECT."""
    c = _SubjectClient(
        {"subject": "Ardea herodias", "verdict": "different_organism", "note": "heron"}
    )
    r = reference_qa.assess_subject(
        c, b"\xff\xd8\xff jpeg", taxon="Carassius auratus", common="goldfish"
    )
    assert r["ok"] is False
    assert r["subject"] == "Ardea herodias"


def test_subject_check_rejects_the_wrong_form_of_the_right_taxon():
    """The dingo/wild-rose case, and the reason a bare taxonomic test cannot catch it: the
    organism is genuinely the claimed taxon but not the morphotype the task asks for."""
    c = _SubjectClient(
        {"subject": "dingo", "verdict": "wrong_form", "note": "not a domestic breed"}
    )
    r = reference_qa.assess_subject(
        c, b"\xff\xd8\xff jpeg", taxon="Canis familiaris", common="dog", morphotype="a domestic dog"
    )
    assert r["ok"] is False


def test_subject_check_passes_a_good_reference():
    c = _SubjectClient(
        {"subject": "Boletus edulis", "verdict": "match", "note": "whole fruiting body"}
    )
    r = reference_qa.assess_subject(
        c, b"\xff\xd8\xff jpeg", taxon="Boletus edulis", common="porcini"
    )
    assert r["ok"] is True


def test_morphotype_reaches_the_model():
    """Without this the rose gallery cannot be fixed: 'Rosa' alone matches a wild dog-rose just
    as well as the garden roses the generators actually produce."""
    c = _SubjectClient({"subject": "Rosa", "verdict": "match", "note": ""})
    reference_qa.assess_subject(
        c,
        b"\xff\xd8\xff jpeg",
        taxon="Rosa",
        common="rose",
        morphotype="a full-petalled garden rose",
    )
    sent = c.seen["messages"][0]["content"][1]["text"]
    assert "full-petalled garden rose" in sent


def test_subject_check_declares_the_real_media_type():
    c = _SubjectClient({"subject": "x", "verdict": "match", "note": ""})
    reference_qa.assess_subject(c, b"\x89PNG\r\n\x1a\n....", taxon="Rosa", common="rose")
    assert c.seen["messages"][0]["content"][0]["source"]["media_type"] == "image/png"


@pytest.mark.parametrize("verdict", ["different_organism", "wrong_form", "not_identifiable"])
def test_every_failing_verdict_fails_the_combiner(verdict):
    out = reference_qa.qa_reference_image(
        subject={"ok": False, "subject": "other", "verdict": verdict, "note": ""}
    )
    assert out["passed"] is False
    assert any("subject" in r for r in out["reasons"])


def test_combiner_still_passes_when_the_subject_is_right():
    out = reference_qa.qa_reference_image(
        composition={"isolated": False},
        subject={"ok": True, "subject": "Boletus edulis", "verdict": "match", "note": ""},
    )
    assert out["passed"] is True


def test_combiner_is_unchanged_when_no_subject_signal_is_supplied():
    """Back-compat: existing callers pass organ/composition/species only."""
    assert reference_qa.qa_reference_image(composition={"isolated": False})["passed"] is True
