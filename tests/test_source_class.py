from app.sourcing import SCAN_DATASETS, source_class


def test_source_class_buckets():
    assert source_class("bio3d-arena") == "ai"
    assert source_class("plant3d") == "scan"
    assert source_class("tomatowur") == "scan"
    assert source_class("objaverse") == "found"
    assert source_class("sketchfab") == "found"
    assert source_class(None) == "found"


def test_scan_registry_has_required_fields():
    for slug, meta in SCAN_DATASETS.items():
        assert {"name", "license", "attribution", "url"} <= set(meta), slug
        assert source_class(slug) == "scan"
