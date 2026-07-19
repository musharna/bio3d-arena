"""The leaderboard's CI whisker bars are positioned as a percent of the board's value span, but
nothing told the reader what that span WAS — the bars showed "there is uncertainty", not "how
much". finalize_rows now stamps the domain (ci_lo/ci_hi) it normalized against so the template can
label the axis, and each row carries a readable win-rate-by-period tooltip for the sparkline.
"""

from app.main import _trend_title
from app.service import finalize_rows


def test_finalize_rows_stamps_ci_domain_on_every_row():
    rows = [
        {"bt_score": 1200.0, "bt_lower": 1100.0, "bt_upper": 1300.0, "generator": "A"},
        {"bt_score": 900.0, "bt_lower": 800.0, "bt_upper": 1000.0, "generator": "B"},
    ]
    out = finalize_rows(rows)
    # Domain is [min(bt_lower), max(bt_upper)] = [800, 1300] — the SAME endpoints the ci_left/
    # ci_width/ci_point percentages were computed against, so an axis drawn from them lines up.
    assert all(r["ci_lo"] == 800.0 for r in out)
    assert all(r["ci_hi"] == 1300.0 for r in out)
    # The stamped domain must actually match the percentage geometry: the row whose lower bound is
    # the domain floor sits at ci_left 0, and the widest upper reaches ci_left+ci_width 100.
    b = next(r for r in out if r["generator"] == "B")
    assert b["ci_left"] == 0.0
    a = next(r for r in out if r["generator"] == "A")
    assert round(a["ci_left"] + a["ci_width"], 1) == 100.0


def test_trend_title_lists_periods_as_percentages():
    t = _trend_title([0.4, 0.55, None, 0.62])
    assert "40%" in t and "62%" in t


def test_trend_title_empty_is_honest_not_fabricated():
    for empty in ([], [None, None]):
        t = _trend_title(empty)
        assert t and "%" not in t  # no fabricated numbers when there is no history
