"""Bounded-sample judge enumeration: connected, capped, excludes Mode-A outputs."""

from __future__ import annotations

from app.database import SessionLocal, init_db
from app.models import Category, Criterion, Generator, JudgeVote, ModelOutput, Task
from tests.factories import cascade_delete


def setup_module(_m):
    init_db()


def _seed(db):
    db.query(JudgeVote).delete()
    cascade_delete(db, ModelOutput, ModelOutput.asset_path.like("smp/%.glb"))
    cascade_delete(db, Task, Task.title == "smp-task")
    cascade_delete(db, Generator, Generator.slug.like("smp-g%"))
    db.query(Category).filter_by(slug="smp-cat").delete(synchronize_session=False)
    db.commit()
    cat = Category(slug="smp-cat", name="C")
    db.add(cat)
    db.flush()
    crit = db.query(Criterion).filter_by(slug="overall").first() or Criterion(
        slug="overall", name="Overall"
    )
    db.add(crit)
    db.flush()
    task = Task(category_id=cat.id, title="smp-task", prompt="p")  # active by default
    db.add(task)
    db.flush()
    ai_ids = []
    for i in range(6):
        g = Generator(slug=f"smp-gai{i}", name=f"AI{i}")
        db.add(g)
        db.flush()
        o = ModelOutput(
            task_id=task.id,
            generator_id=g.id,
            asset_path=f"smp/ai{i}.glb",
            asset_format="glb",
            source="api:fal:trellis",
        )
        db.add(o)
        db.flush()
        ai_ids.append(o.id)
    # one reference scan + one untextured → must be excluded from the sample
    gref = Generator(slug="smp-gref", name="ref")
    gunt = Generator(slug="smp-gunt", name="unt")
    db.add_all([gref, gunt])
    db.flush()
    ref = ModelOutput(
        task_id=task.id,
        generator_id=gref.id,
        asset_path="smp/ref.glb",
        asset_format="glb",
        source="rose-x",
    )
    unt = ModelOutput(
        task_id=task.id,
        generator_id=gunt.id,
        asset_path="smp/unt.glb",
        asset_format="glb",
        source="bio3d-arena",
        meta_json='{"untextured": true}',
    )
    db.add_all([ref, unt])
    db.commit()
    return task, sorted(ai_ids), ref.id, unt.id


def test_enumerate_sample_connected_capped_and_excludes_mode_a():
    import scripts.judge_vlm as jv

    with SessionLocal() as db:
        task, ai_ids, ref_id, unt_id = _seed(db)
        items = jv.enumerate_sample(db, [task.id], criterion_slug="overall", per_output_k=2)

        # two ordered rows per logical pair
        assert len(items) % 2 == 0
        pairs = {tuple(sorted((it["output_a_id"], it["output_b_id"]))) for it in items}
        appeared = {x for p in pairs for x in p}

        # exclusion: reference-scan + untextured outputs never appear
        assert ref_id not in appeared and unt_id not in appeared
        # connectivity: every AI output is covered
        assert appeared == set(ai_ids)
        # capped well below the full grid C(6,2)=15 (circulant n=6,k=2 → 12 pairs)
        assert len(pairs) == 12
        assert len(items) == 24  # 12 pairs × 2 orders
        # everything is the overall criterion, multi4 condition
        assert all(it["criterion_slug"] == "overall" for it in items)
        assert all(it["condition"] == jv.GRID_CONDITION for it in items)


def test_enumerate_sample_skips_tiny_tasks():
    import scripts.judge_vlm as jv

    with SessionLocal() as db:
        # a non-existent task id yields no work (no crash)
        assert jv.enumerate_sample(db, [10**9], criterion_slug="overall") == []


def _seed_gens(db, tag: str, gen_of_output: list[str]):
    """Seed one task whose outputs belong to the named generators (one entry per output).

    `gen_of_output` is e.g. ["a", "a", "a", "b", "c"] → 3 outputs from gen a, 1 from b, 1 from c.
    Returns (task, [output ids in creation order], {output_id: generator_id}).
    """
    db.query(JudgeVote).delete()
    cascade_delete(db, ModelOutput, ModelOutput.asset_path.like(f"{tag}/%.glb"))
    cascade_delete(db, Task, Task.title == f"{tag}-task")
    cascade_delete(db, Generator, Generator.slug.like(f"{tag}-g%"))
    db.query(Category).filter_by(slug=f"{tag}-cat").delete(synchronize_session=False)
    db.commit()

    cat = Category(slug=f"{tag}-cat", name="C")
    db.add(cat)
    db.flush()
    crit = db.query(Criterion).filter_by(slug="overall").first() or Criterion(
        slug="overall", name="Overall"
    )
    db.add(crit)
    db.flush()
    task = Task(category_id=cat.id, title=f"{tag}-task", prompt="p")
    db.add(task)
    db.flush()

    gen_ids: dict[str, int] = {}
    out_ids: list[int] = []
    gen_by_output: dict[int, int] = {}
    for idx, gname in enumerate(gen_of_output):
        if gname not in gen_ids:
            g = Generator(slug=f"{tag}-g{gname}", name=gname.upper())
            db.add(g)
            db.flush()
            gen_ids[gname] = g.id
        o = ModelOutput(
            task_id=task.id,
            generator_id=gen_ids[gname],
            asset_path=f"{tag}/{idx}.glb",
            asset_format="glb",
            source="api:fal:trellis",
        )
        db.add(o)
        db.flush()
        out_ids.append(o.id)
        gen_by_output[o.id] = gen_ids[gname]
    db.commit()
    return task, out_ids, gen_by_output


def _pairs(items):
    return {tuple(sorted((it["output_a_id"], it["output_b_id"]))) for it in items}


def test_enumerate_sample_never_pairs_a_generator_against_itself():
    """A generator owning most of a task's outputs must never be judged against itself."""
    import scripts.judge_vlm as jv

    with SessionLocal() as db:
        # gen "a" owns 5 of 7 outputs — the plain circulant would emit a-vs-a pairs
        task, _out_ids, gen_by_output = _seed_gens(db, "selfp", ["a", "a", "a", "a", "a", "b", "c"])
        items = jv.enumerate_sample(db, [task.id], criterion_slug="overall", per_output_k=3)

        assert items, "expected some pairs"
        for it in items:
            ga = gen_by_output[it["output_a_id"]]
            gb = gen_by_output[it["output_b_id"]]
            assert ga != gb, f"self-pair emitted for generator {ga}"
        # still two ordered rows per logical pair (swap_group preserved)
        assert len(items) == 2 * len(_pairs(items))
        for grp_items in {it["swap_group"] for it in items}:
            rows = [it for it in items if it["swap_group"] == grp_items]
            assert len(rows) == 2
            assert rows[0]["output_a_id"] == rows[1]["output_b_id"]


def test_enumerate_sample_honours_per_output_k_against_distinct_generators():
    """Each output still gets per_output_k comparisons vs DIFFERENT generators when enough exist."""
    import scripts.judge_vlm as jv

    with SessionLocal() as db:
        # 4 generators, "a" owns 3 outputs → every output has ≥3 distinct-generator partners
        task, out_ids, gen_by_output = _seed_gens(db, "budget", ["a", "a", "a", "b", "c", "d"])
        items = jv.enumerate_sample(db, [task.id], criterion_slug="overall", per_output_k=2)
        pairs = _pairs(items)

        degree = {oid: 0 for oid in out_ids}
        partners_gens: dict[int, set[int]] = {oid: set() for oid in out_ids}
        for a, b in pairs:
            degree[a] += 1
            degree[b] += 1
            partners_gens[a].add(gen_by_output[b])
            partners_gens[b].add(gen_by_output[a])

        for oid in out_ids:
            # budget honoured: at least k comparisons, all against other generators
            assert degree[oid] >= 2, f"output {oid} under-compared: {degree[oid]}"
            assert gen_by_output[oid] not in partners_gens[oid]


def test_enumerate_sample_generator_graph_is_connected():
    """BT ranks GENERATORS — the generator-level pair graph must stay connected."""
    import scripts.judge_vlm as jv

    with SessionLocal() as db:
        task, _out_ids, gen_by_output = _seed_gens(
            db, "conn", ["a", "a", "b", "b", "b", "c", "d", "d", "e"]
        )
        items = jv.enumerate_sample(db, [task.id], criterion_slug="overall", per_output_k=2)
        pairs = _pairs(items)

        adj: dict[int, set[int]] = {}
        for a, b in pairs:
            ga, gb = gen_by_output[a], gen_by_output[b]
            assert ga != gb
            adj.setdefault(ga, set()).add(gb)
            adj.setdefault(gb, set()).add(ga)

        all_gens = set(gen_by_output.values())
        assert set(adj) == all_gens, "every generator must appear in at least one pair"

        seen = {next(iter(all_gens))}
        stack = list(seen)
        while stack:
            g = stack.pop()
            for nxt in adj[g]:
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        assert seen == all_gens, "generator graph is disconnected"


def test_enumerate_sample_single_generator_task_yields_no_pairs():
    """A model cannot be compared with itself — degrade to zero pairs, not an error/hang."""
    import scripts.judge_vlm as jv

    with SessionLocal() as db:
        task, _out_ids, _gen_by_output = _seed_gens(db, "solo", ["a", "a", "a", "a"])
        assert jv.enumerate_sample(db, [task.id], criterion_slug="overall", per_output_k=3) == []
