"""A ballot must present every candidate at the SAME level of detail.

The LOD is a transport optimisation: serve a decimated mesh first so the ballot's first frame
arrives sooner, then swap in the full one when a voter looks closely. Whether an output HAS a
decimated companion is decided per mesh, by a byte-size threshold —
`is_lod_candidate` gates on `size_bytes >= LOD_MIN_SOURCE_BYTES` (1 MB, `app/mesh_lod.py`).

Mesh byte size is a property of the GENERATOR. Dense neural meshes clear 1 MB; LLM-authored
procedural meshes do not. So the threshold hands out decimated meshes along generator lines, and
measured on production 2026-08-04 the result was:

  * 86 / 502 visible outputs carried an LOD (17.1%)
  * 6 generators ALWAYS opened decimated (Hunyuan3D x4, Tripo H3.1, TRELLIS 2)
  * 43 generators ALWAYS opened full
  * **113 of 120 served k-ballots (94.2%) put a decimated mesh beside a full-resolution one**

On a fidelity benchmark that is a confound, not a preference: the voter is comparing OUR
decimation of one model against another model's real geometry, and the threshold selects the
heaviest, most detailed meshes — so the models most likely to carry fine structure are exactly
the ones shown degraded at first paint. The card-size delta is small (silhouette IoU 0.9985,
`lod_card_fidelity_2026-08-04`) and the swap-on-zoom does fire, so the exposure is voters who
judge without zooming. Small is not zero, and it is not random with respect to model identity,
which is what makes it a bias rather than noise.

The fix is an invariant rather than a better threshold: ANY threshold on a generator-correlated
quantity reproduces this. The fidelity tier is a property of the BALLOT — if one slot cannot be
served decimated, none are.

This file is the guard. It carries a positive control, because the cheap way to satisfy the
invariant is to stop advertising LODs at all, and that would silently revert a measured
14.4s -> 7.3s payload win.
"""

from __future__ import annotations

import json

import pytest

from app.main import _arena_lod_url, _serialize_output, _uniform_lod_urls
from app.models import ModelOutput


def _out(output_id: int, *, lod: bool) -> ModelOutput:
    """A GLB output that does or does not advertise a decimated companion."""
    return ModelOutput(
        id=output_id,
        asset_format="glb",
        asset_path=f"outputs/{output_id}.glb",
        meta_json=json.dumps({"lod": True} if lod else {"sha256": "x"}),
        source="test",
    )


# --------------------------------------------------------------------------- the invariant


def test_a_mixed_ballot_serves_no_lod_at_all():
    """The defect, stated directly. Three of four slots could be served decimated; because the
    fourth cannot, none are — the voter compares four full-resolution meshes."""
    outs = [_out(1, lod=True), _out(2, lod=True), _out(3, lod=False), _out(4, lod=True)]
    assert _uniform_lod_urls(outs) == [None, None, None, None]


def test_a_uniform_ballot_keeps_every_lod():
    """POSITIVE CONTROL. Satisfying the invariant by never advertising an LOD would pass the test
    above and silently revert the payload win. When every slot can be served decimated, every
    slot is."""
    outs = [_out(11, lod=True), _out(12, lod=True), _out(13, lod=True), _out(14, lod=True)]
    assert _uniform_lod_urls(outs) == [
        "/media/o/11.lod.glb",
        "/media/o/12.lod.glb",
        "/media/o/13.lod.glb",
        "/media/o/14.lod.glb",
    ]


def test_the_rule_is_all_or_nothing_not_majority():
    """A single ineligible slot is enough. The confound does not care how many slots are
    affected — it cares that WHICH slot is degraded is decided by the model in it."""
    outs = [_out(21, lod=True), _out(22, lod=False)]
    assert _uniform_lod_urls(outs) == [None, None]


def test_a_pair_is_governed_by_the_same_rule_as_a_quad():
    """The pairwise ballot is not exempt. It is still two models judged side by side, and
    `?set=pair` remains reachable, so a mixed pair is the same confound with k=2."""
    assert _uniform_lod_urls([_out(31, lod=True), _out(32, lod=True)]) == [
        "/media/o/31.lod.glb",
        "/media/o/32.lod.glb",
    ]
    assert _uniform_lod_urls([_out(33, lod=True), _out(34, lod=False)]) == [None, None]


def test_a_non_glb_slot_forces_the_whole_ballot_to_full_detail():
    """Point clouds are mounted by a different viewer and never carry an LOD, so a ballot holding
    one can never be uniformly decimated. `_arena_lod_url` already returns None for them; this
    pins that it propagates to the ballot rather than being special-cased away."""
    ply = _out(41, lod=True)
    ply.asset_format = "ply"
    assert _arena_lod_url(ply) is None
    assert _uniform_lod_urls([_out(42, lod=True), ply]) == [None, None]


def test_empty_and_single_slot_ballots_do_not_raise():
    """Defensive: the gold path and the pairwise fallback both build ballots from lists whose
    length this helper should not assume."""
    assert _uniform_lod_urls([]) == []
    assert _uniform_lod_urls([_out(51, lod=True)]) == ["/media/o/51.lod.glb"]


# --------------------------------------------------------------------------- the call sites


def test_serialize_output_cannot_compute_its_own_lod_url():
    """The mechanism, not the symptom.

    `_serialize_output` used to call `_arena_lod_url(o)` itself, which is precisely what made the
    tier a property of the mesh instead of the ballot. Requiring the caller to pass the resolved
    url means a future k-wise-like ballot builder CANNOT reintroduce the confound by forgetting —
    it gets a TypeError instead of a biased ballot.
    """
    with pytest.raises(TypeError):
        _serialize_output(_out(61, lod=True))  # type: ignore[call-arg]


def test_serialize_output_carries_the_url_it_is_given():
    o = _out(62, lod=True)
    assert _serialize_output(o, "/media/o/62.lod.glb")["lod_url"] == "/media/o/62.lod.glb"
    assert _serialize_output(o, None)["lod_url"] is None, (
        "the ballot's decision must win; an output that COULD be decimated is still served full "
        "when a slot beside it cannot be"
    )


def test_the_pairwise_payload_a_voter_receives_is_uniform():
    """End to end on the real serializer, because the invariant is only worth anything in the
    JSON a browser actually gets. This is the assertion that fails on the unfixed code: `a` is
    served a decimated mesh and `b` the full one, in the same ballot."""
    from app.main import _serialize
    from app.models import Category, Comparison, Criterion, Task

    task = Task(id=1, title="t", prompt="p", category=Category(id=1, name="Plants", slug="plants"))
    crit = Criterion(id=1, slug="fidelity", name="Fidelity")
    cmp_ = Comparison(id=1, task_id=1, criterion_id=1)

    mixed = _serialize(cmp_, task, crit, _out(71, lod=True), _out(72, lod=False))
    assert mixed["a"]["lod_url"] is None and mixed["b"]["lod_url"] is None, (
        "one slot was served a decimated mesh while the other was served full resolution — the "
        "voter is comparing our decimation against another model's real geometry"
    )

    both = _serialize(cmp_, task, crit, _out(73, lod=True), _out(74, lod=True))
    assert both["a"]["lod_url"] == "/media/o/73.lod.glb"
    assert both["b"]["lod_url"] == "/media/o/74.lod.glb"
