"""Unit test for the `lb_row` macro's VARIANT SEAM (app/templates/leaderboard.html).

The macro already tolerates two optional fields — `r.variant_of` (this row is a variant of
another model: indented, ▸ marker, no board position of its own) and `r.children` (its variants,
rendered recursively at depth+1). NOTHING supplies them today, so no route test can reach that
code: the seam could be broken by an unrelated edit and the whole suite would stay green. This
test drives the macro directly so the shape a follow-on spec will populate is LOCKED now.

The macro source is sliced out of the shipped template (not re-typed here) and compiled on its
own: `leaderboard.html` extends `base.html`, so importing it through a Jinja loader would try to
render the whole page chrome (`request` et al.) just to reach a macro.
"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment

TEMPLATE = Path(__file__).resolve().parents[1] / "app" / "templates" / "leaderboard.html"
MACRO = "lb_row"


def _macro_source() -> str:
    src = TEMPLATE.read_text(encoding="utf-8")
    start = src.index("{%% macro %s(" % MACRO)
    end = src.index("{% endmacro %}", start) + len("{% endmacro %}")
    return src[start:end]


def _render(row: dict, pos: int = 1) -> str:
    # autoescape=True mirrors Starlette's Jinja2Templates (an .html template).
    env = Environment(autoescape=True)  # noqa: S701 - same setting the app uses for .html
    tpl = env.from_string(_macro_source() + "\n{{ %s(row, pos) }}" % MACRO)
    return tpl.render(row=row, pos=pos)


def _row(name: str, **extra) -> dict:
    """Today's row shape (what main._enrich_leaderboard_rows produces)."""
    base = {
        "generator": name,
        "slug": name,
        "avatar": name[:2].upper(),
        "avatar_hue": 120,
        "momentum": "flat",
        "provenance": ["image→3D"],
        "bt_score": 1000.0,
        "bt_lower": 990.0,
        "bt_upper": 1010.0,
        "ci_left": 0.0,
        "ci_width": 50.0,
        "ci_point": 25.0,
        "trend_points": "0,10 64,10",
        "n_games": 42,
        "rank": 1,
        # A separated top-3 rank (main._enrich_leaderboard_rows): CI-tied / unvoted rows get
        # podium=False and show their shared rank number instead of a medal.
        "podium": True,
        "status": {"firm": True, "label": "firm"},
        "detail_url": f"/models/{name}",
    }
    base.update(extra)
    return base


def test_macro_source_is_findable():
    """Guards the slicing above: a renamed/moved macro must fail loudly here, not silently
    stop testing the seam."""
    src = _macro_source()
    assert "r.variant_of" in src and "r.children" in src


def test_plain_row_renders_as_a_normal_board_row():
    html = _render(_row("alpha"), pos=1)
    assert 'class="lb-row"' in html
    assert "lb-row-variant" not in html
    assert "lb-variant-mark" not in html
    assert "lb-medal" in html  # pos 1..3 get a medal
    assert "alpha" in html
    assert html.count("<tr") == 1  # no children -> exactly one row


def test_ci_tied_row_shows_its_shared_rank_number_instead_of_a_medal():
    """Rank 1 is not always a podium: rank_by_ci ties statistically-inseparable models at the same
    rank, and on a thin board that can be EVERY model. Such a row shows the number, not a medal."""
    html = _render(_row("tied", podium=False), pos=1)
    assert "lb-medal" not in html
    assert "lb-rank-num" in html
    assert ">1<" in html  # the shared rank is still displayed


def test_parent_with_children_renders_indented_variant_rows():
    parent = _row("hunyuan", children=[_row("hunyuan-turbo"), _row("hunyuan-standard")])
    html = _render(parent, pos=2)

    rows = html.split("<tr")[1:]
    assert len(rows) == 3, "parent + its two variants"
    assert "lb-row-variant" not in rows[0]  # the parent keeps its board position
    for child in rows[1:]:
        assert "lb-row-variant" in child  # variant class = the indent hook
        assert "lb-variant-mark" in child  # ▸ instead of a rank/medal
        assert "lb-medal" not in child  # a variant holds no board position of its own
        assert "lb-rank-num" not in child
    assert "hunyuan-turbo" in html and "hunyuan-standard" in html


def test_variant_of_marks_a_row_even_at_top_level():
    """`variant_of` is the other half of the seam: a row can declare itself a variant without
    being nested by the parent loop."""
    html = _render(_row("meshy-turbo", variant_of="meshy"), pos=1)
    assert "lb-row-variant" in html
    assert "lb-variant-mark" in html
    assert "lb-medal" not in html


def test_children_nest_recursively():
    grandchild = _row("g2")
    child = _row("g1", children=[grandchild])
    html = _render(_row("g0", children=[child]), pos=1)
    assert html.count("<tr") == 3
    assert html.count("lb-row-variant") == 2  # both descendants, at depth 1 and depth 2
    assert "g2" in html
