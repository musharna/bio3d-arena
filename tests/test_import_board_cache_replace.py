"""Cached leaderboards must be REPLACED on import, not merged by surrogate id.

Hit on a real release. `kingdom_judge_rating` carries a UNIQUE constraint on its natural key —
`(generator_id, kingdom, criterion_id, view_condition)` — while the importer merges by primary
key. An internal board refit reassigns those surrogate ids, so the bundle arrives holding
`id=626` for a scope the live database already stores under a different id, and Postgres rejects
the insert:

    UniqueViolation: duplicate key value violates unique constraint "uq_kingdom_judge_scope"
    Key (generator_id, kingdom, criterion_id, view_condition)=(13, plants, 1, multi4) already exists

This fires on **every import after the first**. The initial release only survived it by going
into an empty database, which is exactly the kind of bug a first deploy cannot reveal.

These tables are pure derived caches — nothing holds a foreign key to them, and their surrogate
ids carry no meaning — so replacing them wholesale is both correct and simpler than teaching the
merge about per-table natural keys.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models import Base, Criterion, Generator, KingdomJudgeRating, Rating
from scripts import import_public


@pytest.fixture
def live(tmp_path):
    """A 'live' database that already holds board rows under DIFFERENT surrogate ids."""
    url = f"sqlite:///{tmp_path / 'live.db'}"
    eng = create_engine(url, future=True)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        s.add(Generator(id=13, slug="g13", name="G13"))
        s.add(Criterion(id=1, slug="overall", name="Overall"))
        s.add(Rating(id=900, generator_id=13, criterion_id=1, elo=1000.0))
        s.add(
            KingdomJudgeRating(
                id=900,
                generator_id=13,
                kingdom="plants",
                criterion_id=1,
                view_condition="multi4",
                bt_score=500.0,
                n_games=3,
            )
        )
        s.commit()
    return url, eng


def _bundle_tables():
    """What a freshly refitted bundle looks like: same scopes, NEW surrogate ids."""
    return {
        "generator": [{"id": 13, "slug": "g13", "name": "G13"}],
        "criterion": [{"id": 1, "slug": "overall", "name": "Overall"}],
        "rating": [{"id": 626, "generator_id": 13, "criterion_id": 1, "elo": 1234.0}],
        "kingdom_judge_rating": [
            {
                "id": 626,
                "generator_id": 13,
                "kingdom": "plants",
                "criterion_id": 1,
                "view_condition": "multi4",
                "bt_score": 959.4,
                "n_games": 8,
            }
        ],
    }


def test_a_refitted_board_replaces_the_live_one(live):
    """The release case. Without the fix this raises UniqueViolation on the natural key."""
    url, eng = live
    import_public.replace_board_caches(eng, _bundle_tables())

    with Session(eng) as s:
        kjr = list(s.execute(select(KingdomJudgeRating)).scalars())
        ratings = list(s.execute(select(Rating)).scalars())
    assert len(kjr) == 1, "the stale board row survived alongside the new one"
    assert kjr[0].bt_score == pytest.approx(959.4), "the live board was not refreshed"
    assert len(ratings) == 1
    assert ratings[0].elo == pytest.approx(1234.0)


def test_a_table_the_bundle_does_not_supply_is_left_alone(live):
    """Positive control AND a real hazard: blanket-clearing would wipe a live board whenever a
    bundle happened to carry no rows for it, turning a partial export into a blank leaderboard."""
    url, eng = live
    import_public.replace_board_caches(eng, {"rating": []})  # no kingdom_judge_rating key at all

    with Session(eng) as s:
        assert len(list(s.execute(select(KingdomJudgeRating)).scalars())) == 1, (
            "a board the bundle never mentioned was cleared"
        )


def test_non_cache_tables_are_untouched(live):
    """Only derived caches may be replaced. Votes, outputs and generators are the record."""
    url, eng = live
    import_public.replace_board_caches(eng, _bundle_tables())
    with Session(eng) as s:
        assert len(list(s.execute(select(Generator)).scalars())) == 1
        assert len(list(s.execute(select(Criterion)).scalars())) == 1
