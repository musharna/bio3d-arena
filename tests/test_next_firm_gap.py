"""A board card should name the NEXT achievable step, not just the current state.

The hub already told a visitor where a board stands ("1 of 13 firm"). It never told them what
it would take to move it, so the number read as a verdict rather than an invitation. As of the
2026-07-27 release audit, 0 of 92 rated entrants are firm, which makes "0 of 20 firm" on every
card the first thing a new visitor sees.

`next_firm_gap` closes that: the votes needed to firm the CLOSEST not-yet-firm entrant on this
board. Deliberately the closest one and not the whole board — the point is a step someone can
believe they'll finish, and the honest total is a number that makes people leave.

Units matter here and are easy to get wrong. A pairwise vote increments `n_games` by one for
BOTH entrants in the pair, so raising ONE entrant to the threshold takes `threshold - n_games`
votes that include it — not half that. (Halving is right for a SET of entrants, where each vote
advances two members at once, and mixing the two up would understate the ask by 2x.)
"""

from __future__ import annotations

from app import service


def _rated(*games: int) -> list[dict]:
    """Leaderboard-row shaped stubs. next_firm_gap only reads n_games, but modality_hub_cards
    also runs the rows through finalize_rows, which needs the BT fields — so carry them."""
    return [
        {
            "n_games": g,
            "generator": f"g{i}",
            "slug": f"g{i}",
            "bt_score": 1000.0 + i,
            "bt_lower": 990.0,
            "bt_upper": 1010.0,
        }
        for i, g in enumerate(games)
    ]


def test_gap_is_the_closest_unfirm_entrant():
    """Not the mean, not the worst — the nearest finish line."""
    rows = _rated(2, 25, 11)  # closest is 25 -> 5 short of 30
    assert service.next_firm_gap(rows) == service.FIRM_VOTE_THRESHOLD - 25


def test_gap_counts_votes_not_half_votes():
    """One vote advances BOTH entrants in the pair by one game, so firming a SINGLE entrant
    costs `threshold - n_games` votes. Halving would understate the ask by 2x."""
    rows = _rated(20)
    assert service.next_firm_gap(rows) == 10  # 30 - 20, NOT 5


def test_no_gap_once_everything_is_firm():
    """None, not 0 — a caller must be able to tell 'nothing left to do' from 'one more vote'."""
    rows = _rated(30, 44)
    assert service.next_firm_gap(rows) is None


def test_firm_entrants_do_not_mask_a_closer_unfirm_one():
    """An over-threshold entrant has a negative 'remaining'; a naive min() over all rows would
    return that and report a nonsense gap."""
    rows = _rated(99, 28)
    assert service.next_firm_gap(rows) == 2


def test_no_rated_entrants_has_no_gap():
    """A board nobody has voted on yet cannot promise a next step."""
    assert service.next_firm_gap([]) is None


def test_unrated_entrants_are_ignored():
    """n_games == 0 means the entrant is in the pool but unevaluated; counting it would make
    every empty board advertise a 30-vote gap it cannot actually deliver on."""
    assert service.next_firm_gap(_rated(0, 0)) is None
    assert service.next_firm_gap(_rated(0, 27)) == 3


def test_hub_cards_carry_the_gap():
    """The card dict is what the template renders, so the field has to reach it."""
    from app import config, paradigms

    visible = [p for p in paradigms.PARADIGMS if p not in config.APP_HIDDEN_PARADIGMS]
    target = visible[0]

    def rows_fn(p):
        return _rated(0, 26) if p == target else []

    cards = service.modality_hub_cards(rows_fn, modalities=visible)
    first = next(c for c in cards if c["paradigm"] == target)
    assert first["next_firm_gap"] == 4
    assert first["firm_count"] == 0 and first["rated_count"] == 1
    # A board with no rated entrants must not advertise a step it cannot deliver.
    other = next((c for c in cards if c["paradigm"] != target), None)
    if other is not None:
        assert other["next_firm_gap"] is None
