from __future__ import annotations

import uuid

from app import matchmaking
from app.models import Generator, ModelOutput, Task


def _out(gen_paradigm, n):
    g = Generator(slug=f"g{uuid.uuid4().hex}", name="g", kind="model", paradigm=gen_paradigm)
    return ModelOutput(generator=g, n_comparisons=n, asset_path="x.glb", is_gold=False)


def _gen(paradigm="procedural_llm"):
    return Generator(slug=f"g{uuid.uuid4().hex}", name="g", kind="model", paradigm=paradigm)


def _out_of(gen, n=0):
    """An output owned by an EXISTING generator (a model usually has several outputs per task)."""
    return ModelOutput(generator=gen, n_comparisons=n, asset_path="x.glb", is_gold=False)


def test_pick_pair_never_pairs_a_generator_against_itself():
    """A model must never be compared to itself: the two outputs must come from DIFFERENT
    generators. Regression: pick_pair only required distinct OUTPUTS, so a generator owning
    several outputs on a task got served as both sides ("TRELLIS vs TRELLIS") and that
    (G, G) match then polluted the Bradley-Terry fit."""
    hog = _gen()  # one generator owns most of the task's outputs
    other = _gen()
    task = Task(title="t", prompt="p", category_id=1)
    task.outputs = [_out_of(hog, 0) for _ in range(5)] + [_out_of(other, 3)]
    for _ in range(60):
        pair = matchmaking.pick_pair(None, task)
        assert pair is not None
        assert pair[0].generator is not pair[1].generator, "served a model against itself"
        assert {pair[0].generator, pair[1].generator} == {hog, other}


def test_pick_pair_none_when_group_has_only_one_generator():
    """A paradigm group with several outputs but a SINGLE generator has no valid pair."""
    only = _gen()
    task = Task(title="t", prompt="p", category_id=1)
    task.outputs = [_out_of(only, 0), _out_of(only, 1), _out_of(only, 2)]
    assert matchmaking.pick_pair(None, task) is None


def test_pick_pair_falls_through_to_a_group_with_two_generators():
    """The least-sampled group is single-generator (unpairable) — pick_pair must not give up;
    it falls through to the other paradigm group."""
    solo = _gen("image_recon")
    a = _gen("procedural_llm")
    b = _gen("procedural_llm")
    task = Task(title="t", prompt="p", category_id=1)
    task.outputs = [_out_of(solo, 0), _out_of(solo, 0), _out_of(a, 5), _out_of(b, 5)]
    for _ in range(20):
        pair = matchmaking.pick_pair(None, task)
        assert pair is not None
        assert {pair[0].generator, pair[1].generator} == {a, b}


def test_pick_task_skips_task_whose_only_group_is_single_generator():
    """pick_task must apply the SAME pairability rule as pick_pair — otherwise it offers a
    task pick_pair then rejects (spurious 404 on /api/next)."""
    only = _gen()
    task = Task(title="t", prompt="p", category_id=1)
    task.outputs = [_out_of(only, 0), _out_of(only, 1)]
    assert matchmaking._fresh_pair_exists(matchmaking._real_outputs(task), set()) is False


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
