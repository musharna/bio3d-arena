from __future__ import annotations

import uuid

from app import matchmaking
from app.models import Generator, ModelOutput, Task


def _out(gen_paradigm, n):
    g = Generator(slug=f"g{uuid.uuid4().hex}", name="g", kind="model", paradigm=gen_paradigm)
    return ModelOutput(generator=g, n_comparisons=n, asset_path="x.glb", is_gold=False)


def test_pick_pair_never_crosses_paradigm():
    task = Task(title="t", prompt="p", category_id=1)
    # 1 image_recon + 2 procedural_llm — only the llm pair is valid
    task.outputs = [_out("image_recon", 0), _out("procedural_llm", 1), _out("procedural_llm", 2)]
    for _ in range(30):
        pair = matchmaking.pick_pair(None, task)
        assert pair is not None
        assert pair[0].generator.paradigm == pair[1].generator.paradigm == "procedural_llm"


def test_pick_pair_none_when_no_same_paradigm_pair():
    task = Task(title="t", prompt="p", category_id=1)
    task.outputs = [_out("image_recon", 0), _out("procedural_llm", 1)]
    assert matchmaking.pick_pair(None, task) is None


def test_pick_pair_rotates_across_tied_paradigms():
    """When several paradigm groups tie at the minimum comparison count, pick_pair must
    rotate fairly across them. Regression: min() broke ties by dict insertion order, so the
    first-inserted paradigm (here image_recon, with many fresh outputs) starved every other
    paradigm of votes forever."""
    task = Task(title="t", prompt="p", category_id=1)
    task.outputs = [_out("image_recon", 0) for _ in range(5)] + [
        _out("procedural_llm", 0) for _ in range(3)
    ]
    seen = set()
    for _ in range(200):
        pair = matchmaking.pick_pair(None, task)
        assert pair is not None
        assert pair[0].generator.paradigm == pair[1].generator.paradigm
        seen.add(pair[0].generator.paradigm)
    assert seen == {"image_recon", "procedural_llm"}, f"starved paradigm(s); only saw {seen}"
