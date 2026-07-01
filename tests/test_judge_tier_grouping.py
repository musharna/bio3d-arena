"""I3b: judge leaderboard + difficulty-tier board must group + rank WITHIN paradigm,
mirroring the human leaderboard's `_leaderboard_rows` pattern — never a single flat
cross-paradigm list (disconnected BT components make cross-paradigm ordering meaningless)."""

from __future__ import annotations

import uuid

from app import service
from app.database import SessionLocal, init_db
from app.difficulty import set_task_difficulty
from app.main import _judge_leaderboard_rows
from app.models import Category, Criterion, Generator, JudgeRating, JudgeVote, ModelOutput, Task


def setup_module(_m):
    init_db()


def _gen(db, paradigm):
    g = Generator(
        slug=f"jtg-{uuid.uuid4().hex}", name=f"g-{uuid.uuid4().hex[:6]}", paradigm=paradigm
    )
    db.add(g)
    db.flush()
    return g


def test_judge_leaderboard_rows_rank_per_paradigm():
    with SessionLocal() as db:
        crit = db.query(Criterion).filter_by(slug="overall").first() or Criterion(
            slug="overall", name="Overall"
        )
        if crit.id is None:
            db.add(crit)
            db.flush()

        a_strong = _gen(db, "image_recon")
        a_weak = _gen(db, "image_recon")
        b_strong = _gen(db, "procedural_llm")
        b_weak = _gen(db, "procedural_llm")

        def rate(gen, bt):
            db.add(
                JudgeRating(
                    generator_id=gen.id,
                    criterion_id=crit.id,
                    view_condition="multi4",
                    elo=1000.0,
                    bt_score=bt,
                    bt_lower=bt - 5,
                    bt_upper=bt + 5,
                    n_games=10,
                    judge_model="claude-sonnet-4-6",
                )
            )

        rate(a_strong, 1200.0)
        rate(a_weak, 900.0)
        rate(b_strong, 1300.0)
        rate(b_weak, 800.0)
        db.commit()

        rows = _judge_leaderboard_rows(db, "overall", "multi4")
        assert rows
        assert all("paradigm" in r for r in rows)

        by_paradigm: dict[str, list[dict]] = {}
        for r in rows:
            by_paradigm.setdefault(r["paradigm"], []).append(r)

        assert "image_recon" in by_paradigm and "procedural_llm" in by_paradigm
        for pgm, grows in by_paradigm.items():
            ranks = sorted(r["rank"] for r in grows)
            assert ranks[0] == 1  # rank restarts at 1 within each paradigm group

        img_names = {r["generator"]: r["rank"] for r in by_paradigm["image_recon"]}
        assert img_names[a_strong.name] == 1
        proc_names = {r["generator"]: r["rank"] for r in by_paradigm["procedural_llm"]}
        assert proc_names[b_strong.name] == 1


def test_tier_perceptual_ranking_rows_carry_paradigm_and_rank_restarts():
    with SessionLocal() as db:
        cat = Category(slug=f"jtg-cat-{uuid.uuid4().hex}", name="C")
        db.add(cat)
        db.flush()
        crit = db.query(Criterion).filter_by(slug="overall").first() or Criterion(
            slug="overall", name="Overall"
        )
        if crit.id is None:
            db.add(crit)
            db.flush()
        task = Task(category_id=cat.id, title=f"jtg-task-{uuid.uuid4().hex}", prompt="p")
        db.add(task)
        db.flush()
        set_task_difficulty(db, task.id, "easy", "", commit=False)

        a1 = _gen(db, "image_recon")
        a2 = _gen(db, "image_recon")
        b1 = _gen(db, "procedural_llm")
        b2 = _gen(db, "procedural_llm")

        def out(gen):
            o = ModelOutput(
                task_id=task.id, generator_id=gen.id, asset_path="seed/x.glb", asset_format="glb"
            )
            db.add(o)
            db.flush()
            return o

        oa1, oa2, ob1, ob2 = out(a1), out(a2), out(b1), out(b2)

        def votes(winner_out, loser_out, n=4):
            for _ in range(n):
                db.add(
                    JudgeVote(
                        criterion_id=crit.id,
                        view_condition="multi4",
                        task_id=task.id,
                        output_a_id=winner_out.id,
                        output_b_id=loser_out.id,
                        winner="a",
                        judge_model="m",
                        swap_group=uuid.uuid4().hex,
                    )
                )

        votes(oa1, oa2)  # a1 beats a2 within paradigm A
        votes(ob1, ob2)  # b1 beats b2 within paradigm B
        db.commit()

        blocks = service.tier_perceptual_ranking(db, "overall", "multi4")
        easy = next(b for b in blocks if b["tier"] == "easy")
        assert easy["rows"]
        assert all("paradigm" in r and "rank" in r for r in easy["rows"])

        by_paradigm: dict[str, list[dict]] = {}
        for r in easy["rows"]:
            by_paradigm.setdefault(r["paradigm"], []).append(r)
        assert "image_recon" in by_paradigm and "procedural_llm" in by_paradigm
        for pgm, grows in by_paradigm.items():
            ranks = sorted(r["rank"] for r in grows)
            assert ranks[0] == 1  # rank restarts at 1 within each paradigm group

        img_rank = {r["generator"]: r["rank"] for r in by_paradigm["image_recon"]}
        assert img_rank[a1.name] == 1
        proc_rank = {r["generator"]: r["rank"] for r in by_paradigm["procedural_llm"]}
        assert proc_rank[b1.name] == 1
