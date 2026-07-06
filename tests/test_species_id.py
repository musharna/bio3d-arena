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


def test_real_forward_pass_if_available():
    if not species_id.available():
        pytest.skip("open_clip not installed")
    buf = io.BytesIO()
    from PIL import Image

    Image.new("RGB", (224, 224), (0, 128, 0)).save(buf, format="PNG")
    bundle = species_id.load_model("clip")
    v = species_id.embed_image(bundle, buf.getvalue())
    assert v.shape[0] > 0 and abs(float(np.linalg.norm(v)) - 1.0) < 1e-3
