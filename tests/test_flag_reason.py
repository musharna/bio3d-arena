import pytest
from pydantic import ValidationError

from app.schemas import FlagIn


def test_flag_default_is_not_the_organism():
    assert FlagIn(output_id=1).reason == "not_the_organism"


def test_flag_accepts_new_reason_rejects_old():
    assert FlagIn(output_id=1, reason="not_the_organism").reason == "not_the_organism"
    assert FlagIn(output_id=1, reason="failed").reason == "failed"
    with pytest.raises(ValidationError):
        FlagIn(output_id=1, reason="not_a_plant")
