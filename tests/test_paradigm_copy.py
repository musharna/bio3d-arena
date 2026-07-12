from app import config, paradigms


def test_what_this_measures_covers_every_visible_paradigm():
    visible = [p for p in paradigms.PARADIGMS if p not in config.APP_HIDDEN_PARADIGMS]
    for p in visible:
        assert p in paradigms.WHAT_THIS_MEASURES, f"missing one-liner for {p}"
        assert paradigms.WHAT_THIS_MEASURES[p].strip()
