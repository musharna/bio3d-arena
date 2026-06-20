"""Vote application + leaderboard recomputation — the glue between votes and ranks.

On each vote we apply an online Elo update for instant feedback. The authoritative
leaderboard is recomputed in batch with Bradley-Terry + bootstrap CIs over the
full decisive-vote record.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config, ranking
from .models import Category, Comparison, Criterion, ModelOutput, Rating, Task, Vote


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


def _matches_for_scope(
    db: Session, criterion_id: int, category_id: int | None, include_ties: bool = True
) -> list[tuple[int, int]]:
    """Decisive (winner_gen, loser_gen) pairs for a (criterion, category) scope.

    A 'tie' is credited as a split — one win in each direction — so ties inform
    Bradley-Terry without a separate tie parameter. 'bad' votes are excluded.
    category_id=None means the global scope (all categories).
    """
    stmt = (
        select(Vote, Comparison)
        .join(Comparison, Vote.comparison_id == Comparison.id)
        .where(Comparison.criterion_id == criterion_id)
    )
    if category_id is not None:
        stmt = stmt.join(Task, Comparison.task_id == Task.id).where(Task.category_id == category_id)

    matches: list[tuple[int, int]] = []
    for vote, comparison in db.execute(stmt).all():
        if vote.winner == "bad":
            continue
        gen_a = db.get(ModelOutput, comparison.output_a_id).generator_id
        gen_b = db.get(ModelOutput, comparison.output_b_id).generator_id
        if vote.winner == "a":
            matches.append((gen_a, gen_b))
        elif vote.winner == "b":
            matches.append((gen_b, gen_a))
        elif vote.winner == "tie" and include_ties:
            matches.append((gen_a, gen_b))
            matches.append((gen_b, gen_a))
    return matches


def _players_for_scope(db: Session, category_id: int | None) -> list[int]:
    """All generators eligible to appear in a scope's leaderboard (so 0-game ones show)."""
    stmt = select(ModelOutput.generator_id)
    if category_id is not None:
        stmt = stmt.join(Task, ModelOutput.task_id == Task.id).where(
            Task.category_id == category_id
        )
    return sorted({gid for gid in db.execute(stmt).scalars().all()})


def recompute_scope(
    db: Session, criterion: Criterion, category_id: int | None, commit: bool = True
) -> dict:
    """Refit Bradley-Terry for one (criterion, category) scope and cache Rating rows."""
    matches = _matches_for_scope(db, criterion.id, category_id)
    players = sorted(set(_players_for_scope(db, category_id)) | {p for m in matches for p in m})
    result = ranking.bradley_terry(players, matches, bootstrap=config.BT_BOOTSTRAP)
    for gid in players:
        rating = get_or_create_rating(db, gid, criterion.id, category_id)
        rating.bt_score = result.scores.get(gid, ranking.BT_BASE)
        rating.bt_lower = result.lower.get(gid, ranking.BT_BASE)
        rating.bt_upper = result.upper.get(gid, ranking.BT_BASE)
        rating.n_games = int(result.n_games.get(gid, 0))
    if commit:
        db.commit()
    return {"matches": len(matches), "players": len(players)}


def recompute_leaderboard(db: Session, criterion_slug: str = "overall") -> dict:
    """Backward-compatible single-criterion GLOBAL recompute."""
    criterion = (
        db.execute(select(Criterion).where(Criterion.slug == criterion_slug)).scalars().first()
    )
    if criterion is None:
        return {"status": "no-such-criterion"}
    detail = recompute_scope(db, criterion, category_id=None)
    return {"status": "ok", **detail}


def recompute_all(db: Session) -> dict:
    """Recompute every (criterion × {global + each category}) leaderboard scope."""
    criteria = db.execute(select(Criterion)).scalars().all()
    categories = db.execute(select(Category)).scalars().all()
    n_scopes = 0
    for criterion in criteria:
        recompute_scope(db, criterion, category_id=None, commit=False)
        n_scopes += 1
        for cat in categories:
            recompute_scope(db, criterion, category_id=cat.id, commit=False)
            n_scopes += 1
    db.commit()
    return {
        "status": "ok",
        "scopes": n_scopes,
        "criteria": len(criteria),
        "categories": len(categories),
    }
