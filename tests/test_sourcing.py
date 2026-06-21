from app.sourcing import classify_license, label_depiction, public_safe


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


def test_classify_license_excludes_arr_and_unmarked():
    for lic in ["All Rights Reserved", "", None, "Standard", "Proprietary"]:
        assert classify_license(lic) == "exclude", lic


def test_public_safe_only_cc0_by_sa():
    assert public_safe("CC0") and public_safe("CC-BY") and public_safe("CC-BY-SA")
    for lic in ["CC-BY-NC", "CC-BY-ND", "CC-BY-NC-SA", "All Rights Reserved", None]:
        assert not public_safe(lic), lic


def test_label_depiction():
    assert label_depiction("Tomato plant in a pot") == "whole_plant"
    assert label_depiction("tomato seedling") == "whole_plant"
    assert label_depiction("Ripe red tomato") == "fruit"
    assert label_depiction("cherry tomatoes") == "fruit"
    assert label_depiction("tomato leaf closeup") == "leaf"
    assert label_depiction("tomato soup can") == "other"
