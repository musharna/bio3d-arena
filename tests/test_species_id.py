import io

import numpy as np
import pytest
from app import species_id


class _FakeBundle:
    """Stand-in for (model, preprocess, tokenizer): zero_shot() is tested via monkeypatched
    _logits so the label→softmax mapping is exercised without torch/open_clip installed."""


def test_zero_shot_softmax_sums_to_one(monkeypatch):
    monkeypatch.setattr(species_id, "_logits", lambda bundle, png, labels: np.array([2.0, 0.0]))
    out = species_id.zero_shot(_FakeBundle(), b"\x89PNG", ["a", "b"])
    assert set(out) == {"a", "b"}
    assert abs(sum(out.values()) - 1.0) < 1e-6
    assert out["a"] > out["b"]


def test_species_rep_score_is_first_label_prob(monkeypatch):
    monkeypatch.setattr(species_id, "_logits", lambda bundle, png, labels: np.array([3.0, 0.0]))
    s = species_id.species_rep_score(
        _FakeBundle(), b"\x89PNG", common="tomato", taxon="Solanum lycopersicum"
    )
    assert 0.9 < s <= 1.0


def test_classify_species_ranks_top_and_margin(monkeypatch):
    # panel of 3 taxa; logits favor index 1 -> that taxon is top, with a positive margin.
    monkeypatch.setattr(
        species_id, "_logits", lambda bundle, png, labels: np.array([0.0, 4.0, 1.0])
    )
    out = species_id.classify_species(
        _FakeBundle(), b"\x89PNG", ["Zea mays", "Rosa", "Pinus sylvestris"]
    )
    assert out["top"] == "Rosa"
    assert out["prob"] > out["ranked"][1][1]  # top beats runner-up
    assert out["margin"] > 0
    assert [t for t, _ in out["ranked"]] == ["Rosa", "Pinus sylvestris", "Zea mays"]


#: Substrings that mark "the weights could not be FETCHED" as opposed to "the model is broken".
#:
#: The distinction is the whole point. `_if_available` used to check only whether open_clip was
#: INSTALLED, then called load_model(), which downloads ~1.7 GB from the HF hub. On 2026-08-01 a
#: Hugging Face CAS outage failed that download and turned an unrelated PR's CI red:
#:
#:     FileNotFoundError: Failed to download file (open_clip_pytorch_model.bin) ...
#:     CAS Client Error ... cas-server.xethub.hf.co
#:
#: Matching on the message is stringly-typed and I would rather it were not, but open_clip wraps
#: every transport failure in a bare FileNotFoundError/RuntimeError, so the exception TYPE cannot
#: distinguish "HF is down" from "the model loaded and produced garbage". Being narrow is what
#: keeps this from becoming a blanket except: anything not matching still fails the build.
_FETCH_FAILURE_MARKERS = (
    "failed to download",
    "cas client error",
    "connection",
    "timed out",
    "timeout",
    "temporarily unavailable",
    "503",
    "504",
    "max retries",
    "name resolution",
)


def _is_fetch_failure(e: BaseException) -> bool:
    msg = f"{type(e).__name__}: {e}".lower()
    return any(m in msg for m in _FETCH_FAILURE_MARKERS)


#: The VERBATIM failure from the 2026-08-01 CI run, kept as a fixture so the classifier is tested
#: against the string that actually broke the build rather than one I invented to match my own
#: markers.
_REAL_CI_FAILURE = (
    "Failed to download file (open_clip_pytorch_model.bin) for "
    "laion/CLIP-ViT-L-14-laion2B-s32B-b82K. Last error: Task error: File reconstruction error: "
    "CAS Client Error: Request middleware error: error sending request for url "
    "(https://cas-server.xethub.hf.co/v2/reconstructions/ae1de3f6)"
)


def test_the_real_outage_message_is_classified_as_a_fetch_failure():
    """Positive control, using the exact exception that reddened CI."""
    assert _is_fetch_failure(FileNotFoundError(_REAL_CI_FAILURE))


@pytest.mark.parametrize(
    "exc",
    [
        AssertionError("embedding norm 0.42 != 1.0"),
        ValueError("expected 768 dims, got 512"),
        RuntimeError("Given groups=1, weight of size [1024, 3, 14, 14], expected input[1, 4,...]"),
        KeyError("logit_scale"),
    ],
)
def test_a_genuine_model_defect_is_NOT_swallowed(exc):
    """Negative control, and the one that matters. A skip guard is only acceptable if it cannot
    hide a real regression — if any of these classified as 'infrastructure', the test would go
    silently green while the model was broken."""
    assert not _is_fetch_failure(exc)


def test_real_forward_pass_if_available():
    """Real forward pass against the actual CLIP weights — the only check here that would catch a
    genuine breakage in load_model/embed_image, since every other test in this file monkeypatches
    `_logits` and never touches torch.

    CI caches the HF hub (see .github/workflows/ci.yml), so the normal path is a cache hit and no
    network at all. The skip below is for the residual case only: a cold cache during an upstream
    outage. It must stay NARROW — a broken model still fails."""
    if not species_id.available():
        pytest.skip("open_clip not installed")
    buf = io.BytesIO()
    from PIL import Image

    Image.new("RGB", (224, 224), (0, 128, 0)).save(buf, format="PNG")
    try:
        bundle = species_id.load_model("clip")
    except Exception as e:  # noqa: BLE001 — re-raised below unless it is a fetch failure
        if not _is_fetch_failure(e):
            raise
        pytest.skip(f"CLIP weights unavailable (upstream fetch failed), not a code defect: {e}")
    v = species_id.embed_image(bundle, buf.getvalue())
    assert v.shape[0] > 0 and abs(float(np.linalg.norm(v)) - 1.0) < 1e-3
