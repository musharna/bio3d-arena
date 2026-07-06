from app.licensing import normalize_license


def test_space_forms_map_to_spdx():
    assert normalize_license("CC-BY 4.0") == "CC-BY-4.0"
    assert normalize_license("CC BY 4.0") == "CC-BY-4.0"
    assert normalize_license("CC0 1.0") == "CC0-1.0"
    assert normalize_license("CC0") == "CC0-1.0"
    assert normalize_license("CC-BY-SA 4.0") == "CC-BY-SA-4.0"
    assert normalize_license("CC-BY 3.0") == "CC-BY-3.0"


def test_objaverse_codes():
    assert normalize_license("by") == "CC-BY-4.0"
    assert normalize_license("cc0") == "CC0-1.0"
    assert normalize_license("by-sa") == "CC-BY-SA-4.0"


def test_already_spdx_unchanged():
    assert normalize_license("CC-BY-4.0") == "CC-BY-4.0"
    assert normalize_license("CC0-1.0") == "CC0-1.0"


def test_nc_nd_normalize_but_stay_nonredistributable():
    from app.public_export import REDISTRIBUTABLE_LICENSES

    assert normalize_license("CC-BY-NC-ND 4.0") == "CC-BY-NC-ND-4.0"
    assert normalize_license("by-nc") == "CC-BY-NC-4.0"
    assert normalize_license("CC-BY-NC-ND-4.0") not in REDISTRIBUTABLE_LICENSES
    assert normalize_license("CC-BY-NC-4.0") not in REDISTRIBUTABLE_LICENSES


def test_none_and_freeform():
    assert normalize_license(None) is None
    assert normalize_license("") is None
    # a wordy provider string is not laundered into an allowlisted id
    assert normalize_license("Hunyuan3D v2 (fal) generated-asset terms") not in {
        "CC-BY-4.0",
        "CC0-1.0",
    }
