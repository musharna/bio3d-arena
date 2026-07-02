# tests/test_dgen_ab.py
import io

from PIL import Image

from app.dgen_ab import composite_ab, ab_prompt, judge_pair, verdict_both_orders, aggregate


def _png(color, size=(8, 8)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def test_composite_ab_is_wider_side_by_side_png():
    out = composite_ab(_png((0, 128, 0)), _png((128, 0, 0)))
    im = Image.open(io.BytesIO(out))
    assert im.width >= 16  # both 8-wide halves side by side
    assert im.height >= 8


def test_ab_prompt_is_blind_and_asks_A_or_B():
    p = ab_prompt("Glycine max", "soybean")
    assert "soybean" in p
    low = p.lower()
    assert '"a"' in low or " a " in low  # asks for an A/B answer
    assert "baseline" not in low and "refined" not in low and "round" not in low  # no leak


def test_judge_pair_parses_A_B_and_none():
    assert judge_pair(lambda prompt, png: "A", b"x", "Rosa", "rose") == "A"
    assert judge_pair(lambda prompt, png: "The better one is B.", b"x", "Rosa", "rose") == "B"
    assert judge_pair(lambda prompt, png: "neither/unclear", b"x", "Rosa", "rose") is None


def test_verdict_both_orders():
    assert verdict_both_orders("B", "A") == "refined"  # refined won both orders
    assert verdict_both_orders("A", "B") == "baseline"  # baseline won both
    assert verdict_both_orders("A", "A") == "inconsistent"  # flipped
    assert verdict_both_orders("B", None) == "inconsistent"


def test_aggregate_rates_over_ab_denominator():
    rows = [
        {"bucket": "ab", "verdict": "refined"},
        {"bucket": "ab", "verdict": "refined"},
        {"bucket": "ab", "verdict": "baseline"},
        {"bucket": "ab", "verdict": "inconsistent"},
        {"bucket": "repair"},
        {"bucket": "no-refinement"},
    ]
    a = aggregate(rows)
    assert a["n_ab"] == 4
    assert a["refined"] == 2 and a["baseline"] == 1 and a["inconsistent"] == 1
    assert a["refined_rate"] == 0.5  # 2/4
    assert a["repairs"] == 1 and a["no_refinement"] == 1
