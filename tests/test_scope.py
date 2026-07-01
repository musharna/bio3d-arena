# tests/test_scope.py
from __future__ import annotations

from app import scope


def _tr(key, cls):
    return {"key": key, "trait_class": cls}


def test_required_parts_whole_plant_classes():
    for cls in ("habit", "phyllotaxy", "proportion"):
        assert scope.required_parts(_tr("plant_x", cls)) == {"whole_plant"}


def test_required_parts_inflorescence_needs_flower():
    assert scope.required_parts(_tr("inflorescence_form", "inflorescence")) == {"flower"}


def test_required_parts_presence_needs_nothing():
    assert scope.required_parts(_tr("fruit_present", "presence")) == set()


def test_required_parts_organ_shape_and_color_derive_organ_from_key():
    cases = {
        ("leaf_form", "organ_shape"): "foliage",
        ("needle_form", "organ_shape"): "foliage",
        ("rosette_leaf_form", "organ_shape"): "foliage",
        ("fruit_form", "organ_shape"): "fruit",
        ("pod_form", "organ_shape"): "fruit",
        ("cone_form", "organ_shape"): "cone",
        ("stem_form", "organ_shape"): "stem_or_trunk",
        ("fruit_color_ripe", "color"): "fruit",
        ("flower_color", "color"): "flower",
        ("flower_pigmentation", "color"): "flower",
        ("foliage_color", "color"): "foliage",
        ("bark_color", "color"): "stem_or_trunk",
    }
    for (key, cls), part in cases.items():
        assert scope.required_parts(_tr(key, cls)) == {part}, (key, cls)


def test_is_assessable_none_scope_fails_open():
    assert scope.is_assessable(None, _tr("plant_habit", "habit")) is True


def test_is_assessable_non_plant_excludes_everything():
    s = {"is_plant": False, "visible_parts": []}
    assert scope.is_assessable(s, _tr("fruit_form", "organ_shape")) is False
    assert scope.is_assessable(s, _tr("fruit_present", "presence")) is False


def test_is_assessable_single_fruit_model():
    """A detached tomato fruit: fruit traits + presence judgeable; whole-plant traits are not."""
    s = {"is_plant": True, "visible_parts": ["fruit"]}
    assert scope.is_assessable(s, _tr("fruit_form", "organ_shape")) is True
    assert scope.is_assessable(s, _tr("fruit_color_ripe", "color")) is True
    assert scope.is_assessable(s, _tr("fruit_present", "presence")) is True
    assert scope.is_assessable(s, _tr("plant_habit", "habit")) is False
    assert scope.is_assessable(s, _tr("leaf_form", "organ_shape")) is False
    assert scope.is_assessable(s, _tr("inflorescence_form", "inflorescence")) is False


def test_is_assessable_whole_plant_implies_foliage_and_stem_not_fruit():
    s = {"is_plant": True, "visible_parts": ["whole_plant"]}
    assert scope.is_assessable(s, _tr("plant_habit", "habit")) is True
    assert scope.is_assessable(s, _tr("leaf_form", "organ_shape")) is True  # foliage implied
    assert scope.is_assessable(s, _tr("stem_form", "organ_shape")) is True  # stem implied
    assert scope.is_assessable(s, _tr("fruit_form", "organ_shape")) is False  # fruit NOT implied
    assert scope.is_assessable(s, _tr("inflorescence_form", "inflorescence")) is False  # no flower


def test_is_assessable_flowering_whole_plant_with_fruit():
    s = {"is_plant": True, "visible_parts": ["whole_plant", "flower", "fruit"]}
    assert scope.is_assessable(s, _tr("inflorescence_form", "inflorescence")) is True
    assert scope.is_assessable(s, _tr("fruit_form", "organ_shape")) is True


def _fake_resp(tool_input):
    from types import SimpleNamespace

    block = SimpleNamespace(type="tool_use", name="record_scope", input=tool_input)
    return SimpleNamespace(content=[block])


def test_parse_scope_extracts_and_filters_unknown_parts():
    resp = _fake_resp(
        {"is_plant": True, "visible_parts": ["fruit", "banana", "fruit"], "rationale": "a tomato"}
    )
    out = scope.parse_scope(resp)
    assert out["is_plant"] is True
    assert out["visible_parts"] == ["fruit"]  # unknown 'banana' dropped, deduped
    assert out["rationale"] == "a tomato"


def test_parse_scope_raises_without_tool_block():
    from types import SimpleNamespace

    resp = SimpleNamespace(content=[SimpleNamespace(type="text", text="hi")])
    try:
        scope.parse_scope(resp)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
