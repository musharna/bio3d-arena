from app.models import Metric
from app.spotlight import derive_flags


def _m(**kw):
    m = Metric(output_id=1, status=kw.pop("status", "ok"))
    for k, v in kw.items():
        setattr(m, k, v)
    return m


def test_unscored_when_no_metric_or_error():
    assert derive_flags(None) == [("unscored", "no objective score")]
    assert derive_flags(_m(status="error")) == [("unscored", "no objective score")]


def test_within_band_is_ok():
    flags = derive_flags(
        _m(chamfer=0.12, gt_band_lo=0.10, gt_band_hi=0.14, coverage=0.8, fscore=0.8)
    )
    assert ("ok", "within natural variation") in flags


def test_chamfer_above_band_is_shape_fail():
    flags = derive_flags(
        _m(chamfer=0.18, gt_band_lo=0.10, gt_band_hi=0.14, coverage=0.8, fscore=0.8)
    )
    assert ("shape", "outside natural variation") in flags
    assert ("ok", "within natural variation") not in flags


def test_low_coverage_and_low_fscore_flagged():
    flags = derive_flags(
        _m(chamfer=0.12, gt_band_lo=0.10, gt_band_hi=0.14, coverage=0.41, fscore=0.40)
    )
    kinds = {k for k, _ in flags}
    assert "coverage" in kinds
    assert "surface" in kinds
