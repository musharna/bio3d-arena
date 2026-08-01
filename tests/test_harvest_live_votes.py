"""The harvest's write path, including the guard that has never fired in anger.

Today's run had zero primary-key collisions — study's max ids happened to sit below the
live-only range — which means the refusal branch protecting internal rows from being overwritten
executed exactly never. A safety guard nobody has watched fail is not a safety guard, and this
one stands between a future harvest and silently rewriting real votes.

`plan()` is not exercised here: it queries the public Postgres with `= any(:ids)`, which SQLite
cannot parse, and standing up a Postgres in CI to test a query shape would buy less than it
costs. `apply()` is where the damage would happen, so that is what is pinned.
"""

from __future__ import annotations

import sqlite3

import pytest

from scripts import harvest_live_votes as harvest

_SCHEMA = """
create table comparison (
    id integer primary key, task_id integer, output_a_id integer, output_b_id integer,
    criterion_id integer, session_id text, is_gold integer default 0
);
create table vote (
    id integer primary key, comparison_id integer references comparison(id),
    winner text, session_id text
);
"""


@pytest.fixture
def study(tmp_path):
    p = tmp_path / "study.db"
    con = sqlite3.connect(p)
    con.executescript(_SCHEMA)
    con.execute("insert into comparison (id, task_id, session_id) values (1, 7, 'old')")
    con.execute(
        "insert into vote (id, comparison_id, winner, session_id) values (1, 1, 'a', 'old')"
    )
    con.commit()
    con.close()
    return p


def _plan(votes, comparisons, *, vote_collisions=(), comparison_collisions=()):
    return {
        "votes": votes,
        "comparisons": comparisons,
        "vote_collisions": list(vote_collisions),
        "comparison_collisions": list(comparison_collisions),
        "columns": {
            "comparison": [
                "id",
                "task_id",
                "output_a_id",
                "output_b_id",
                "criterion_id",
                "session_id",
                "is_gold",
            ],
            "vote": ["id", "comparison_id", "winner", "session_id"],
        },
    }


def test_harvest_inserts_comparisons_and_votes(study):
    p = _plan(
        votes=[{"id": 2, "comparison_id": 9, "winner": "b", "session_id": "live"}],
        comparisons=[{"id": 9, "task_id": 7, "session_id": "live", "is_gold": 0}],
    )
    res = harvest.apply(study, p)
    assert res["votes_total"] == 2
    assert res["comparisons_total"] == 2
    con = sqlite3.connect(study)
    # The vote must resolve to its comparison — inserting votes before comparisons would leave
    # a dangling reference that only surfaces when a board is fitted.
    dangling = con.execute(
        "select count(*) from vote v left join comparison c on c.id = v.comparison_id "
        "where c.id is null"
    ).fetchone()[0]
    con.close()
    assert dangling == 0


def test_a_colliding_vote_id_is_refused(study):
    """The guard. Vote id 1 already exists internally; overwriting it would replace a real
    recorded vote with a different one and no one would ever know."""
    p = _plan(
        votes=[{"id": 1, "comparison_id": 9, "winner": "b", "session_id": "live"}],
        comparisons=[{"id": 9, "task_id": 7, "session_id": "live", "is_gold": 0}],
        vote_collisions=[1],
    )
    with pytest.raises(harvest.HarvestConflict) as e:
        harvest.apply(study, p)
    assert "1" in str(e.value)

    con = sqlite3.connect(study)
    row = con.execute("select winner, session_id from vote where id = 1").fetchone()
    total = con.execute("select count(*) from vote").fetchone()[0]
    con.close()
    assert row == ("a", "old"), "the pre-existing vote was modified despite the refusal"
    assert total == 1, "rows were written despite the refusal"


def test_a_colliding_comparison_id_is_refused(study):
    p = _plan(
        votes=[{"id": 2, "comparison_id": 1, "winner": "b", "session_id": "live"}],
        comparisons=[{"id": 1, "task_id": 99, "session_id": "live", "is_gold": 0}],
        comparison_collisions=[1],
    )
    with pytest.raises(harvest.HarvestConflict):
        harvest.apply(study, p)
    con = sqlite3.connect(study)
    task_id = con.execute("select task_id from comparison where id = 1").fetchone()[0]
    con.close()
    assert task_id == 7, "the pre-existing comparison was overwritten despite the refusal"


def test_gold_comparisons_survive_the_harvest(study):
    """Gold is an attention check, not a ranking signal. It must come across intact so trust
    scoring still works — and stay flagged so the fit keeps excluding it."""
    p = _plan(
        votes=[{"id": 2, "comparison_id": 9, "winner": "a", "session_id": "live"}],
        comparisons=[{"id": 9, "task_id": 7, "session_id": "live", "is_gold": 1}],
    )
    harvest.apply(study, p)
    con = sqlite3.connect(study)
    assert con.execute("select is_gold from comparison where id = 9").fetchone()[0] == 1
    con.close()


def test_a_column_the_internal_schema_lacks_is_dropped_not_fatal(study):
    """The public schema can carry a column the study schema does not. Intersecting the two is
    what keeps a harvest from exploding on an unrelated migration."""
    p = _plan(
        votes=[{"id": 2, "comparison_id": 9, "winner": "b", "session_id": "live", "ballot_id": 4}],
        comparisons=[{"id": 9, "task_id": 7, "session_id": "live", "is_gold": 0}],
    )
    res = harvest.apply(study, p)  # must not raise on the unknown `ballot_id`
    assert res["votes_total"] == 2
