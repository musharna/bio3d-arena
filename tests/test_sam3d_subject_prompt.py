"""SAM-3D is a promptable segment-then-reconstruct model: on a clean single-subject photo its
auto-segmentation returns no masks, so it needs a plain-language subject as extra_input["prompt"].
apply_subject_prompts injects that per-crop subject for prompt-requiring providers only; every
other provider (Meshy 6, Pixal3D, TRELLIS, ...) passes through untouched."""

from __future__ import annotations

from scripts.generate_api_recon import CROPS, PROMPT_REQUIRING_SLUGS, apply_subject_prompts


def test_injects_subject_prompt_for_prompt_requiring_provider():
    seen = {}

    def fake_sam(img, *, api_key, **kw):
        seen.update(kw)
        return b"glb"

    def fake_meshy(img, *, api_key, **kw):
        return b"glb"

    providers = {
        "fal:sam-3d": (fake_sam, "FAL_KEY", "SAM 3D (fal)"),
        "fal:meshy-v6": (fake_meshy, "FAL_KEY", "Meshy 6 (fal)"),
    }
    out = apply_subject_prompts(providers, "tomato plant")

    # sam-3d's fn now forwards the subject as a segmentation prompt
    out["fal:sam-3d"][0](b"img", api_key="k")
    assert seen == {"extra_input": {"prompt": "tomato plant"}}
    # env var + display name preserved
    assert out["fal:sam-3d"][1] == "FAL_KEY"
    assert out["fal:sam-3d"][2] == "SAM 3D (fal)"
    # non-prompt provider passes through by identity (no wrapping)
    assert out["fal:meshy-v6"][0] is fake_meshy


def test_missing_subject_leaves_providers_untouched():
    providers = {"fal:sam-3d": (lambda *a, **k: b"", "FAL_KEY", "SAM 3D (fal)")}
    assert apply_subject_prompts(providers, None) is providers
    assert apply_subject_prompts(providers, "") is providers


def test_sam3d_is_prompt_requiring_and_every_crop_has_a_subject():
    assert "fal:sam-3d" in PROMPT_REQUIRING_SLUGS
    # every crop carries a non-empty plain-language subject so SAM-3D can run across the roster
    for name, crop in CROPS.items():
        assert crop.get("subject"), name
