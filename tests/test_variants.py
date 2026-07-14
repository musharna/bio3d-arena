# tests/test_variants.py
"""Re-hosts of one model must not occupy several leaderboard ranks.

`trellis`, `fal:trellis` and `replicate:trellis` are ONE model on three hosts. Ranked independently
they take three separate rank slots and push genuine rivals down the board. They now render
indented under one representative and hold no rank — while keeping their own BT score and votes,
because pooling votes across hosts is a claim we have not tested.

The representative is ELECTED from the members actually on the board, not pinned in the map: the
local `trellis` checkpoint has NO row on the live image->3D board, so a map naming it the parent
would have grouped nothing while looking correct.
"""

from app.variants import FAMILY_OF, family_of, has_family, nest_variants


def _row(slug, bt, n_games=10):
    return {
        "slug": slug,
        "generator": slug,
        "bt_score": bt,
        "bt_lower": bt - 5,
        "bt_upper": bt + 5,
        "n_games": n_games,
    }


def test_family_map_is_self_consistent():
    """Every member maps to a key that is itself a member — no dangling family."""
    for slug, fam in FAMILY_OF.items():
        assert fam in FAMILY_OF.values()
        assert family_of(slug) == fam


def test_family_of_and_has_family():
    assert family_of("fal:trellis") == "trellis"
    assert family_of("trellis") == "trellis"
    assert family_of("fal:hyper3d") == "fal:hyper3d"  # no siblings -> its own family
    assert has_family("replicate:trellis")
    assert not has_family("fal:hyper3d")


def test_rehosts_fold_under_one_representative():
    rows = [_row("trellis", 1200), _row("fal:trellis", 1150), _row("replicate:trellis", 1100)]

    top = nest_variants(rows)

    assert [r["slug"] for r in top] == ["trellis"]
    assert [c["slug"] for c in top[0]["children"]] == ["fal:trellis", "replicate:trellis"]
    assert all(c["variant_of"] == "trellis" for c in top[0]["children"])


def test_representative_is_elected_when_the_named_canonical_is_absent():
    """The live board has NO plain `trellis` row. The family must still collapse, led by the
    highest-BT member present — otherwise the fix silently does nothing on the real board."""
    rows = [_row("fal:trellis", 1150), _row("replicate:trellis", 1100), _row("fal:hyper3d", 1050)]

    top = nest_variants(rows)

    assert {r["slug"] for r in top} == {"fal:trellis", "fal:hyper3d"}
    rep = next(r for r in top if r["slug"] == "fal:trellis")
    assert [c["slug"] for c in rep["children"]] == ["replicate:trellis"]


def test_effort_variants_group_under_the_same_model():
    """GPT-5.6 Sol and Sol Pro are the SAME underlying model at two effort tiers (OpenRouter says
    so outright). "Does more effort produce a better mesh?" is a real question — but the two must
    not read as two competitors on the board."""
    rows = [
        _row("openrouter-openai-gpt-5-6-sol-pro", 1300),
        _row("openrouter-openai-gpt-5-6-sol", 1200),
        _row("fal:hyper3d", 1100),
    ]

    top = nest_variants(rows)

    assert {r["slug"] for r in top} == {"openrouter-openai-gpt-5-6-sol-pro", "fal:hyper3d"}
    rep = top[0]
    assert [c["slug"] for c in rep["children"]] == ["openrouter-openai-gpt-5-6-sol"]
    assert rep["rank"] == 1 and top[1]["rank"] == 2  # the pair takes ONE slot, not two


def test_effort_variant_slug_matches_what_the_ingester_produces():
    """The family map is keyed by GENERATOR SLUG, which commission.slug_for_model derives from the
    model id. A typo here fails silently — the models simply never group."""
    from app.commission import slug_for_model

    assert slug_for_model("openai/gpt-5.6-sol") == "openrouter-openai-gpt-5-6-sol"
    assert slug_for_model("openai/gpt-5.6-sol-pro") == "openrouter-openai-gpt-5-6-sol-pro"
    assert family_of(slug_for_model("openai/gpt-5.6-sol-pro")) == slug_for_model(
        "openai/gpt-5.6-sol"
    )


def test_variants_do_not_consume_rank_slots():
    """The distortion: with 3 TRELLIS hosts ranked independently, a rival 4th by score reads as
    4th. Once the hosts collapse into one entrant, it is genuinely 2nd."""
    rows = [
        _row("trellis", 1200),
        _row("fal:trellis", 1150),
        _row("replicate:trellis", 1100),
        _row("fal:hyper3d", 1050),
    ]

    top = nest_variants(rows)

    assert {r["slug"]: r["rank"] for r in top} == {"trellis": 1, "fal:hyper3d": 2}


def test_a_variant_never_carries_a_medal():
    rows = [_row("trellis", 1200), _row("fal:trellis", 1190)]

    top = nest_variants(rows)

    assert top[0]["podium"] is True
    assert top[0]["children"][0]["podium"] is False


def test_variant_keeps_its_own_score_and_votes():
    """Grouping is presentational. Pooling votes across hosts would be an untested claim."""
    rows = [_row("trellis", 1200, n_games=40), _row("fal:trellis", 1150, n_games=7)]

    top = nest_variants(rows)
    child = top[0]["children"][0]

    assert (child["bt_score"], child["n_games"]) == (1150, 7)
    assert top[0]["n_games"] == 40


def test_no_row_is_ever_dropped():
    rows = [_row("trellis", 1200), _row("fal:trellis", 1150), _row("fal:hyper3d", 1050)]

    top = nest_variants(rows)

    shown = {r["slug"] for r in top} | {c["slug"] for r in top for c in r.get("children", [])}
    assert shown == {"trellis", "fal:trellis", "fal:hyper3d"}


def test_non_variant_board_is_unchanged():
    rows = [_row("fal:hyper3d", 1200), _row("fal:triposr", 1100)]

    top = nest_variants(rows)

    assert [r["slug"] for r in top] == ["fal:hyper3d", "fal:triposr"]
    assert all(not r.get("children") for r in top)
    assert all(r["variant_of"] is None for r in top)
