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


def test_one_redistribution_allowlist_for_outputs_and_reference_photos():
    """Outputs and recon input photos are gated by the SAME question -- may we redistribute this?
    export_public only runs the reference gate when posture == 'redistribute'. The two gates were
    hand-maintained literals that drifted in BOTH directions (the export set had CC-BY-2.0 /
    PUBLIC-DOMAIN / ODbL-1.0 the reference set lacked; the reference set had CC-BY-SA-3.0 the
    export set lacked), and nothing failed. They must be the same object, not equal copies."""
    from app import public_export, reference_provenance
    from app.licensing import REDISTRIBUTABLE_LICENSES

    assert public_export.REDISTRIBUTABLE_LICENSES is REDISTRIBUTABLE_LICENSES
    assert reference_provenance.REDISTRIBUTABLE_LICENSES is REDISTRIBUTABLE_LICENSES


def test_allowlist_admits_only_free_redistributable_licenses():
    """Every member permits redistribution (attribution/share-alike are conditions, not bars).
    NC and ND do bar it, so they must never appear."""
    from app.licensing import REDISTRIBUTABLE_LICENSES

    assert REDISTRIBUTABLE_LICENSES == {
        "CC0-1.0",
        "PUBLIC-DOMAIN",
        "CC-BY-2.0",
        "CC-BY-3.0",
        "CC-BY-4.0",
        "CC-BY-SA-3.0",
        "CC-BY-SA-4.0",
        "ODbL-1.0",
    }
    assert not [x for x in REDISTRIBUTABLE_LICENSES if "-NC" in x or "-ND" in x]


def test_none_and_freeform():
    assert normalize_license(None) is None
    assert normalize_license("") is None
    # a wordy provider string is not laundered into an allowlisted id
    assert normalize_license("Hunyuan3D v2 (fal) generated-asset terms") not in {
        "CC-BY-4.0",
        "CC0-1.0",
    }
