"""Vote application + leaderboard recomputation — the glue between votes and ranks.

On each vote we apply an online Elo update for instant feedback. The authoritative
leaderboard is recomputed in batch with Bradley-Terry + bootstrap CIs over the
full decisive-vote record.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config, ranking
from .models import Comparison, Criterion, ModelOutput, Rating, Vote


def get_or_create_rating(
    db: Session, generator_id: int, criterion_id: int, category_id: int | None = None
) -> Rating:
    stmt = select(Rating).where(
        Rating.generator_id == generator_id,
        Rating.criterion_id == criterion_id,
        Rating.category_id.is_(None) if category_id is None else Rating.category_id == category_id,
    )
    rating = db.execute(stmt).scalars().first()
    if rating is None:
        rating = Rating(
            generator_id=generator_id, criterion_id=criterion_id, category_id=category_id
        )
        db.add(rating)
        db.flush()
    return rating


def apply_vote(db: Session, vote: Vote) -> None:
    """Record bookkeeping for a vote: bump comparison counts + online Elo.

    Elo is updated on the global (category-agnostic) scope for the comparison's
    criterion. 'bad' votes are recorded but do not move Elo.
    """
    comparison = db.get(Comparison, vote.comparison_id)
    out_a = db.get(ModelOutput, comparison.output_a_id)
    out_b = db.get(ModelOutput, comparison.output_b_id)
    out_a.n_comparisons += 1
    out_b.n_comparisons += 1

    if vote.winner == "bad":
        return

    score_a = {"a": 1.0, "b": 0.0, "tie": 0.5}[vote.winner]
    ra = get_or_create_rating(db, out_a.generator_id, comparison.criterion_id)
    rb = get_or_create_rating(db, out_b.generator_id, comparison.criterion_id)
    new_a, new_b = ranking.elo_update(ra.elo, rb.elo, score_a, k=config.ELO_K)
    ra.elo, rb.elo = new_a, new_b
    ra.n_games += 1
    rb.n_games += 1


def recompute_leaderboard(db: Session, criterion_slug: str = "overall") -> dict:
    """Refit Bradley-Terry over all decisive votes for a criterion and cache results.

    Updates the global (category_id=NULL) Rating rows' bt_score/bt_lower/bt_upper.
    """
    criterion = (
        db.execute(select(Criterion).where(Criterion.slug == criterion_slug)).scalars().first()
    )
    if criterion is None:
        return {"status": "no-such-criterion"}

    # Pull decisive votes joined to the generators behind each output.
    rows = db.execute(
        select(Vote, Comparison)
        .join(Comparison, Vote.comparison_id == Comparison.id)
        .where(Comparison.criterion_id == criterion.id)
    ).all()

    # Build (winner_gen, loser_gen) decisive matches.
    matches: list[tuple[int, int]] = []
    for vote, comparison in rows:
        if vote.winner not in ("a", "b"):
            continue
        gen_a = db.get(ModelOutput, comparison.output_a_id).generator_id
        gen_b = db.get(ModelOutput, comparison.output_b_id).generator_id
        if vote.winner == "a":
            matches.append((gen_a, gen_b))
        else:
            matches.append((gen_b, gen_a))

    # All generators that have a rating row for this criterion (so everyone shows).
    rating_rows = (
        db.execute(
            select(Rating).where(Rating.criterion_id == criterion.id, Rating.category_id.is_(None))
        )
        .scalars()
        .all()
    )
    players = sorted({r.generator_id for r in rating_rows} | {p for m in matches for p in m})

    result = ranking.bradley_terry(players, matches, bootstrap=config.BT_BOOTSTRAP)

    for gid in players:
        rating = get_or_create_rating(db, gid, criterion.id)
        rating.bt_score = result.scores.get(gid, ranking.BT_BASE)
        rating.bt_lower = result.lower.get(gid, ranking.BT_BASE)
        rating.bt_upper = result.upper.get(gid, ranking.BT_BASE)
        rating.n_games = int(result.n_games.get(gid, 0))
    db.commit()
    return {"status": "ok", "matches": len(matches), "players": len(players)}
