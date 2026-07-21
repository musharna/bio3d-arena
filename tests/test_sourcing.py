from app.sourcing import (
    VOLUMETRIC_DATASETS,
    classify_license,
    label_depiction,
    public_safe,
    source_class,
)


def test_classify_license_hosts_cc_and_public_domain():
    for lic in [
        "CC0",
        "CC-BY 4.0",
        "CC BY-SA",
        "cc-by-nc",
        "CC-BY-NC-ND",
        "CC-BY-ND",
        "Creative Commons Attribution",
        "Public Domain",
    ]:
        assert classify_license(lic) == "host", lic


def test_classify_license_hosts_sketchfab_short_codes():
    # Real values returned by objaverse.load_annotations()["license"]
    for lic in ["cc0", "by", "by-sa", "by-nc", "by-nc-sa", "by-nc-nd", "by-nd"]:
        assert classify_license(lic) == "host", lic


def test_classify_license_excludes_arr_and_unmarked():
    for lic in ["All Rights Reserved", "", None, "Standard", "Proprietary", "st", "ed"]:
        assert classify_license(lic) == "exclude", lic


def test_public_safe_only_cc0_by_sa():
    assert public_safe("CC0") and public_safe("CC-BY") and public_safe("CC-BY-SA")
    for lic in ["CC-BY-NC", "CC-BY-ND", "CC-BY-NC-SA", "All Rights Reserved", None]:
        assert not public_safe(lic), lic


def test_public_safe_sketchfab_short_codes():
    # Short codes: cc0/by/by-sa → safe; by-nc/by-nd/by-nc-sa/by-nc-nd → not safe
    assert public_safe("cc0")
    assert public_safe("by")
    assert public_safe("by-sa")
    assert not public_safe("by-nc")
    assert not public_safe("by-nd")
    assert not public_safe("by-nc-sa")
    assert not public_safe("by-nc-nd")
    assert not public_safe("st")


def test_label_depiction():
    assert label_depiction("Tomato plant in a pot") == "whole_plant"
    assert label_depiction("tomato seedling") == "whole_plant"
    assert label_depiction("Ripe red tomato") == "fruit"
    assert label_depiction("cherry tomatoes") == "fruit"
    assert label_depiction("tomato leaf closeup") == "leaf"
    assert label_depiction("tomato soup can") == "other"


def test_source_class_volumetric():
    assert source_class("mri:ipk-barley-mri") == "volumetric"
    assert source_class("ct:some-dataset") == "volumetric"
    # existing classes still resolve
    assert source_class("crops3d") == "scan"
    assert source_class("bio3d-arena") == "ai"
    assert source_class("procedural:lpy") == "procedural"


def test_volumetric_dataset_registry_barley():
    d = VOLUMETRIC_DATASETS["ipk-barley-mri"]
    assert d["license"] == "CC-BY 4.0"
    assert d["modality"] == "MRI"
    assert "10.5447/IPK/2017/10" in d["url"]
