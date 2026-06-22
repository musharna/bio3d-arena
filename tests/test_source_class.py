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


def test_source_class_api_is_ai():
    from app.sourcing import source_class

    assert source_class("api:tripo") == "ai"
    assert source_class("api:meshy") == "ai"
    assert source_class("bio3d-arena") == "ai"  # unchanged
    assert source_class("plant3d") == "scan"  # unchanged
    assert source_class("objaverse") == "found"  # unchanged
    assert source_class(None) == "found"  # unchanged


def test_source_class_infinigen_is_procedural():
    from app.sourcing import source_class

    assert source_class("infinigen") == "procedural"
    assert source_class("api:tripo") == "ai"  # unchanged
    assert source_class("plant3d") == "scan"  # unchanged
    assert source_class("objaverse") == "found"  # unchanged


def test_source_class_procedural_prefix():
    from app.sourcing import source_class

    assert source_class("procedural:helios") == "procedural"
    assert source_class("procedural:agrigen") == "procedural"
    assert source_class("infinigen") == "procedural"  # unchanged
    assert source_class("api:tripo") == "ai"  # unchanged
    assert source_class("plant3d") == "scan"  # unchanged
    assert source_class("objaverse") == "found"  # unchanged
