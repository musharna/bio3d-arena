import uuid
from app import matchmaking
from app.database import SessionLocal, init_db
from app.models import Generator, ModelOutput, Task


def setup_module(_m):
    init_db()


def _task_with(db, paradigm_counts):
    t = Task(title=f"q-{uuid.uuid4().hex[:8]}", prompt="p", category_id=1)
    db.add(t)
    db.flush()
    for para, n in paradigm_counts.items():
        for _ in range(n):
            g = Generator(slug=f"g-{uuid.uuid4().hex}", name="g", kind="model", paradigm=para)
            db.add(g)
            db.flush()
            db.add(
                ModelOutput(task_id=t.id, generator_id=g.id, asset_path="x.glb", asset_format="glb")
            )
    db.flush()
    return t


def _task_with_gen_counts(db, paradigm, outputs_per_generator):
    """A task whose `paradigm` group has len(outputs_per_generator) generators, the i-th of
    which owns outputs_per_generator[i] outputs."""
    t = Task(title=f"q-{uuid.uuid4().hex[:8]}", prompt="p", category_id=1)
    db.add(t)
    db.flush()
    for n in outputs_per_generator:
        g = Generator(slug=f"g-{uuid.uuid4().hex}", name="g", kind="model", paradigm=paradigm)
        db.add(g)
        db.flush()
        for _ in range(n):
            db.add(
                ModelOutput(task_id=t.id, generator_id=g.id, asset_path="x.glb", asset_format="glb")
            )
    db.flush()
    return t


def test_quad_has_four_distinct_generators():
    """The 4-up ballot must show FOUR different models. Regression: pick_quad only required
    4 distinct outputs, so one generator could appear twice — and resolve_kballot then derived
    a (G, G) pairwise comparison from that ballot, polluting Bradley-Terry."""
    with SessionLocal() as db:
        # 8 outputs in the group, but only 4 generators — one owns 5 of them.
        t = _task_with_gen_counts(db, "procedural", [5, 1, 1, 1])
        for _ in range(30):
            quad = matchmaking.pick_quad(db, t)
            assert quad is not None and len(quad) == 4
            assert len({o.generator_id for o in quad}) == 4, "same model twice on one ballot"
        db.rollback()


def test_none_when_fewer_than_four_distinct_generators():
    """Plenty of outputs, only 3 generators → no admissible quad (caller falls back to pairwise)."""
    with SessionLocal() as db:
        t = _task_with_gen_counts(db, "procedural", [4, 4, 4])  # 12 outputs, 3 generators
        assert matchmaking.pick_quad(db, t) is None
        db.rollback()


def test_quad_falls_through_to_group_with_four_generators():
    """A group can have >=4 outputs but <4 generators; pick_quad must skip it, not fail."""
    with SessionLocal() as db:
        t = Task(title=f"q-{uuid.uuid4().hex[:8]}", prompt="p", category_id=1)
        db.add(t)
        db.flush()
        # procedural: 6 outputs / 2 generators (unqualified). text_native: 4 outputs / 4 gens.
        for para, per_gen in (("procedural", [3, 3]), ("text_native", [1, 1, 1, 1])):
            for n in per_gen:
                g = Generator(slug=f"g-{uuid.uuid4().hex}", name="g", kind="model", paradigm=para)
                db.add(g)
                db.flush()
                for _ in range(n):
                    db.add(
                        ModelOutput(
                            task_id=t.id,
                            generator_id=g.id,
                            asset_path="x.glb",
                            asset_format="glb",
                        )
                    )
        db.flush()
        for _ in range(10):
            quad = matchmaking.pick_quad(db, t)
            assert quad is not None
            assert {o.generator.paradigm for o in quad} == {"text_native"}
            assert len({o.generator_id for o in quad}) == 4
        db.rollback()


def test_quad_returned_when_four_same_paradigm():
    with SessionLocal() as db:
        t = _task_with(db, {"procedural": 4})
        quad = matchmaking.pick_quad(db, t)
        assert quad is not None and len(quad) == 4
        assert len({o.id for o in quad}) == 4
        assert len({o.generator.paradigm for o in quad}) == 1
        db.rollback()


def test_none_when_fewer_than_four_in_any_group():
    with SessionLocal() as db:
        t = _task_with(db, {"procedural": 3, "text_native": 3})  # no single group has 4
        assert matchmaking.pick_quad(db, t) is None
        db.rollback()


def test_exclude_fn_applied_before_counting():
    with SessionLocal() as db:
        t = _task_with(db, {"procedural": 5})
        outs = sorted(matchmaking._real_outputs(t), key=lambda o: o.id)
        drop = {outs[0].id, outs[1].id}
        assert matchmaking.pick_quad(db, t, exclude_fn=lambda o: o.id in drop) is None
        db.rollback()


def test_seen_quads_skips_the_only_quad_in_an_exact_group_of_four():
    # A task with exactly 4 same-paradigm outputs has exactly one possible quad. Once
    # that quad's frozenset of ids is in seen_quads, pick_quad has nothing left to serve.
    with SessionLocal() as db:
        t = _task_with(db, {"procedural": 4})
        quad = matchmaking.pick_quad(db, t)
        assert quad is not None
        seen = {frozenset(o.id for o in quad)}
        assert matchmaking.pick_quad(db, t, seen_quads=seen) is None
        db.rollback()


def test_seen_quads_drops_the_whole_group_when_group_has_five_or_more():
    # Pins CURRENT behavior (mirrors pick_pair's drop-the-group fallthrough, but pick_quad
    # does NOT try alternate 4-combinations within a >4 group the way pick_pair tries
    # alternate pairs -- once the least-sampled quad is seen, the whole group is dropped
    # and pick_quad returns None even though a fresh quad (e.g. swapping in the 5th
    # member) still exists in principle).
    with SessionLocal() as db:
        t = _task_with(db, {"procedural": 5})
        outs = sorted(matchmaking._real_outputs(t), key=lambda o: o.id)
        # Bump one output's n_comparisons so the "least-sampled 4" are deterministic
        # regardless of the internal random shuffle (all others stay at the default 0).
        outs[4].n_comparisons = 5
        db.flush()
        least_sampled_four = frozenset(o.id for o in outs[:4])
        assert matchmaking.pick_quad(db, t, seen_quads={least_sampled_four}) is None
        db.rollback()
