"""Post-vote engagement: overall_rank_map gives each generator its current standing on the
overall Mode-A board (cached Rating table), so the reveal can show "this model ranks #N". Cheap
read; reference/hidden generators are excluded; unrated generators are absent."""

from __future__ import annotations

import random

from sqlalchemy import select

from app import service
from app.database import SessionLocal, init_db
from app.models import Criterion, Generator, ModelOutput, Rating, Task


def _rated_gen(db, name: str, source: str, bt: float, crit_id: int) -> Generator:
    r = random.randint(0, 10**9)
    g = Generator(slug=f"rank-{name}-{r}", name=f"{name}{r}")
    db.add(g)
    db.flush()
    t = Task(title=f"t-{r}", prompt="p", category_id=1)
    db.add(t)
    db.flush()
    db.add(
        ModelOutput(
            task_id=t.id, generator_id=g.id, asset_path="x.glb", asset_format="glb", source=source
        )
    )
    db.add(
        Rating(
            criterion_id=crit_id,
            category_id=None,
            generator_id=g.id,
            elo=1000.0,
            bt_score=bt,
            bt_lower=bt - 1,
            bt_upper=bt + 1,
            n_games=5,
        )
    )
    db.flush()
    return g


def test_overall_rank_map_orders_by_bt_and_excludes_refs():
    init_db()
    with SessionLocal() as db:
        crit = db.execute(select(Criterion).where(Criterion.slug == "overall")).scalars().first()
        if crit is None:
            crit = Criterion(slug="overall", name="Overall")
            db.add(crit)
            db.flush()
        # very high bt_scores so this trio sits at the top regardless of any other seeded ratings
        hi = _rated_gen(db, "Hi", "api:fal:x", 9_000.0, crit.id)
        mid = _rated_gen(db, "Mid", "api:fal:y", 8_000.0, crit.id)
        lo = _rated_gen(db, "Lo", "api:fal:z", 7_000.0, crit.id)
        ref = _rated_gen(db, "Ref", "rose-x", 9_500.0, crit.id)  # reference scan → excluded
        db.commit()

        rm = service.overall_rank_map(db)
        assert ref.id not in rm  # GT/reference scans don't compete on the Mode-A board
        assert hi.id in rm and mid.id in rm and lo.id in rm
        # ordered by bt_score desc
        assert rm[hi.id][0] < rm[mid.id][0] < rm[lo.id][0]
        # (rank, total) — total is the count of ranked (non-ref) generators, shared across the trio
        assert rm[hi.id][1] == rm[lo.id][1] >= 3
