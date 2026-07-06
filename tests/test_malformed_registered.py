from app.completeness_validation import _SEVERITY


def test_malformed_has_severity_between_partial_and_complete():
    assert "malformed" in _SEVERITY
    assert _SEVERITY["partial-organism"] < _SEVERITY["malformed"] < _SEVERITY["complete"]
