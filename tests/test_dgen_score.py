# tests/test_dgen_score.py
from app.dgen import score_glb


def _fake_capture(glb_abs, azimuths, elev):
    # one tiny 1x1 PNG per azimuth (real PNG bytes so tile_contact_sheet can open them)
    import io
    from PIL import Image

    out = []
    for _ in azimuths:
        buf = io.BytesIO()
        Image.new("RGB", (4, 4), (0, 128, 0)).save(buf, format="PNG")
        out.append(buf.getvalue())
    return out


class _TraitClient:
    def __init__(self):
        self.messages = self

    def create(self, **kw):
        class B:
            type = "tool_use"
            name = "record_traits"
            input = {
                "traits": [
                    {"trait_key": "leaf_form", "verdict": "present_correct"},
                    {"trait_key": "has_pod", "verdict": "absent"},
                ]
            }

        class R:
            content = [B()]

        return R()


class _CompClient:
    def __init__(self):
        self.messages = self

    def create(self, **kw):
        class B:
            type = "tool_use"
            name = "record_completeness"
            input = {
                "organs_present": [
                    {"key": "vegetative_axis", "status": "present"},
                    {"key": "foliage", "status": "present"},
                    {"key": "reproductive_pod", "status": "absent"},
                ],
                "note": "ok",
            }

        class R:
            content = [B()]

        return R()


def test_score_glb_renders_and_scores():
    traits = [
        {
            "key": "leaf_form",
            "trait_class": "organ_shape",
            "expected": "trifoliate",
            "visual": "leaflets",
        },
        {"key": "has_pod", "trait_class": "presence", "expected": "pods", "visual": "pods"},
    ]
    out = score_glb(
        "/tmp/whatever.glb",
        taxon="Glycine max",
        prompt="a soybean plant",
        traits=traits,
        capture_multi=_fake_capture,
        trait_client=_TraitClient(),
        completeness_client=_CompClient(),
    )
    assert out["fidelity"] == 0.5  # 1 present_correct of 2 assessable
    assert out["completeness_category"] == "complete"  # axis+foliage present
    assert out["completeness_missing_organs"] == []  # both required present
    assert isinstance(out["sheet_png"], bytes)
