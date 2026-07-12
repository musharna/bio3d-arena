from app import service


def test_firm_status_firm_at_threshold():
    n = service.FIRM_VOTE_THRESHOLD
    assert service.firm_status(n) == {"firm": True, "label": "firm"}
    assert service.firm_status(n + 5)["firm"] is True


def test_firm_status_counts_remaining_below_threshold():
    n = service.FIRM_VOTE_THRESHOLD - 3
    s = service.firm_status(n)
    assert s["firm"] is False
    assert s["label"] == "3 more votes → firm"


def test_firm_status_singular_vote_remaining():
    n = service.FIRM_VOTE_THRESHOLD - 1
    s = service.firm_status(n)
    assert s["firm"] is False
    assert s["label"] == "1 more vote → firm"
