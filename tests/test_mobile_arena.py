# tests/test_mobile_arena.py
import pathlib
import re

from fastapi.testclient import TestClient

from app.main import app

CSS_PATH = pathlib.Path(__file__).resolve().parents[1] / "app" / "static" / "style.css"
CSS = CSS_PATH.read_text()

# The arena's phone breakpoint. Matches the file's existing convention (see the other
# max-width: 720px block); tests read the rules out of the real stylesheet rather than
# asserting on a hand-maintained copy.
PHONE_BP = "@media (max-width: 720px)"


def _media_blocks(css: str, header: str) -> list[tuple[int, str]]:
    """Every `header { ... }` block in `css`, as (start_offset, body).

    Brace-balanced, so nested rule bodies inside the media block are captured whole.
    """
    blocks = []
    for m in re.finditer(re.escape(header) + r"\s*\{", css):
        i = m.end() - 1  # at the opening brace
        depth = 0
        for j in range(i, len(css)):
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
                if depth == 0:
                    blocks.append((m.start(), css[i + 1 : j]))
                    break
    return blocks


def _phone_css(css: str = CSS) -> str:
    blocks = _media_blocks(css, PHONE_BP)
    assert blocks, f"no {PHONE_BP} block in style.css"
    return "\n".join(body for _, body in blocks)


def _rule(css: str, selector: str) -> str:
    """Body of the first `selector { ... }` rule in css ('' when absent)."""
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    return m.group(1) if m else ""


def test_ab_toggle_markup_present():
    # The arena moved to "/arena" (Task 9 of the design refresh); "/" is now the landing page.
    html = TestClient(app).get("/arena").text
    assert 'class="ab-toggle"' in html
    assert 'data-ab="a"' in html and 'data-ab="b"' in html
    # A is the default-active model column
    assert "model-col is-active" in html


def test_css_is_served():
    """The stylesheet the browser actually gets is the one these tests assert against."""
    res = TestClient(app).get("/static/style.css")
    assert res.status_code == 200
    assert PHONE_BP in res.text


def test_next_pair_button_hidden_before_a_vote():
    """`#next-pair-btn { display: block }` (1,0,0) beats the UA's `[hidden]{display:none}`.

    Without an explicit `[hidden]` rule the "Next pair →" button rendered on page load,
    before any vote — a skip-the-ballot control sitting live on the page. Regression guard
    for that cascade trap.
    """
    assert "display: none" in _rule(CSS, "#next-pair-btn[hidden]")


def test_next_pair_button_is_reachable_on_phones():
    """Post-vote the vote bar is display:none'd and #next-pair-btn replaces it.

    In normal flow that button lands at the end of the document — ~800px below the fold on
    a phone — so a voter tapped a choice and had nothing actionable on screen. It must be
    pinned into the slot the vote bar vacated.
    """
    body = _rule(_phone_css(), "#next-pair-btn")
    assert "position: sticky" in body
    assert "bottom: 0" in body


def test_kwise_mobile_rule_can_win_the_cascade():
    """The K-wise grid's base rule is `.kwise-grid:not([hidden])` (specificity 0,2,0).

    A phone rule written as a bare `.kwise-grid` (0,1,0) LOSES — media queries add no
    specificity — so it silently does nothing and 4 stages stayed at their desktop
    proportions. The mobile rule must carry the same `:not([hidden])` qualifier.
    """
    phone = _phone_css()
    assert ".kwise-grid:not([hidden])" in phone, (
        "mobile .kwise-grid rule must use :not([hidden]) or it loses to the base rule"
    )
    # The 4-up stages are shortened so the whole ballot fits a phone screen.
    assert "height" in _rule(phone, ".kwise-cell .viewer-slot")


def test_phone_tap_targets_meet_44px():
    """iOS/Android minimum touch target. The vote buttons shipped at 41px."""
    phone = _phone_css()
    for selector in (".vote-bar .vote-btn", ".vote-btn.kwise-pick-btn", ".ab-btn"):
        body = _rule(phone, selector)
        m = re.search(r"min-height:\s*(\d+)px", body)
        assert m, f"{selector} has no min-height on phones"
        assert int(m.group(1)) >= 44, f"{selector} tap target {m.group(1)}px < 44px"


def test_viewer_stage_does_not_trap_page_scroll():
    """viewer.js sets touch-action on <model-viewer>, but 3Dmol stages get none.

    Without `pan-y` on the slot itself a vertical drag on a 3Dmol stage is swallowed by the
    viewer and the page won't scroll.
    """
    assert "touch-action: pan-y" in _rule(_phone_css(), ".viewer-slot")


def test_pair_stacks_on_phones():
    """Two 3D stages side-by-side at 390px would be unusable slivers."""
    assert "grid-template-columns: 1fr" in _rule(_phone_css(), ".pair")


def test_phone_overrides_come_after_the_rules_they_override():
    """Source-order guard for the trap that ate the hero + reference-gallery fixes.

    A media query adds NO specificity. `.arena-hero-h1` is declared at ~:2811, so an
    equal-specificity `.arena-hero-h1` inside a media block placed EARLIER in the file
    never applies. These overrides must therefore appear after their base declarations —
    a plain "is the rule present?" substring test passes even when the rule is dead.
    """
    for selector in (".arena-hero-h1", ".arena-hero-lede", ".arena-hero-stats", ".arena-hero"):
        base = CSS.index(f"\n{selector} {{")  # top-level (column-0) declaration
        overrides = [
            start for start, body in _media_blocks(CSS, PHONE_BP) if selector + " {" in body
        ]
        assert overrides, f"{selector} has no phone override"
        assert max(overrides) > base, (
            f"phone override for {selector} precedes its base rule -> dead CSS"
        )


def test_reference_gallery_scrolls_inside_itself():
    """The page body must never scroll sideways; the wide photo row scrolls in its own box."""
    assert "overflow-x: auto" in _rule(CSS, ".reference-gallery")
    # Arena-scoped shrink (spotlight shares the class but not the #id, and keeps full size).
    body = _rule(_phone_css(), "#reference-panel .reference-img")
    m = re.search(r"height:\s*(\d+)px", body)
    assert m, "#reference-panel .reference-img has no phone height"
    assert int(m.group(1)) <= 100, "reference thumbs must shrink from their 150px base on phones"


def test_arena_hero_collapses_on_phones():
    """A phone voter must land on the TASK, not on marketing copy.

    The full hero (h1 + lede + stat row) is 203px tall at 390px wide, which left the model
    stage — the only thing a voter is there to judge — 72px of visible height above the
    sticky vote bar on first paint. The pitch already lives on the home page; /arena is the
    working surface. On phones the lede and the stat row go away and the h1 shrinks to a
    single compact line, putting the whole 300px stage above the vote bar (measured).
    """
    phone = _phone_css()
    assert "display: none" in _rule(phone, ".arena-hero-lede"), "hero lede must be hidden on phones"
    assert "display: none" in _rule(phone, ".arena-hero-stats"), (
        "hero stat row must be hidden on phones"
    )
    # The heading itself stays — the page keeps a title — but compact (base is 34px/26px).
    m = re.search(r"font-size:\s*(\d+)px", _rule(phone, ".arena-hero-h1"))
    assert m, ".arena-hero-h1 has no phone font-size"
    assert int(m.group(1)) <= 20, f"phone hero h1 is {m.group(1)}px — not a compact single line"


def test_served_css_carries_the_hero_collapse():
    """Guard the artifact the browser actually gets, not just the file on disk.

    (The desktop hero must survive: these rules live inside the phone media block, so the
    top-level .arena-hero-lede/.arena-hero-stats declarations are untouched.)
    """
    served = TestClient(app).get("/static/style.css").text
    phone = _phone_css(served)
    assert "display: none" in _rule(phone, ".arena-hero-lede")
    assert "display: none" in _rule(phone, ".arena-hero-stats")
    # Desktop still renders both: the base rules are outside any media block.
    assert "\n.arena-hero-lede {" in served and "\n.arena-hero-stats {" in served
    assert "display: flex" in _rule(served, ".arena-hero-stats")
