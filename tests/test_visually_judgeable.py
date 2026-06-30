from app.traits import is_visually_judgeable, judgeable_reason


def _t(expected, key="k", trait_class="organ_shape", taxon="Solanum lycopersicum"):
    return {"key": key, "trait_class": trait_class, "expected": expected, "taxon": taxon}


def test_accepts_concrete_static_morphology():
    assert is_visually_judgeable(_t("red berry"))
    assert is_visually_judgeable(_t("alternate", trait_class="phyllotaxy"))
    assert is_visually_judgeable(_t("climber", trait_class="habit"))
    assert is_visually_judgeable(_t("trifoliate"))


def test_rejects_empty_or_unstated():
    assert not is_visually_judgeable(_t(""))
    assert not is_visually_judgeable(_t("not explicitly stated"))
    assert judgeable_reason(_t("unknown")) is not None


def test_rejects_temporal():
    assert not is_visually_judgeable(_t("accelerated floral transition"))
    assert not is_visually_judgeable(_t("earlier flowering", trait_class="presence"))
    assert not is_visually_judgeable(_t("degreening during maturation", trait_class="color"))


def test_rejects_comparative_without_baseline():
    assert not is_visually_judgeable(_t("altered leaf morphology"))
    assert not is_visually_judgeable(_t("reduced height", trait_class="proportion"))
    assert not is_visually_judgeable(_t("thickened"))


def test_rejects_microscopic_or_internal():
    assert not is_visually_judgeable(_t("glandular (multicellular)"))
    assert not is_visually_judgeable(_t("lower RGB brightness", trait_class="color"))
    assert not is_visually_judgeable(
        _t("variable (mutations affecting morphology)", key="ovary_morphology")
    )


def test_rejects_wrong_taxon_token():
    assert not is_visually_judgeable(
        _t("post-genital fusion", key="commelina_erecta_sheath", taxon="Zea mays")
    )
    assert not is_visually_judgeable(
        _t("continuous variation in circularity", key="flake_circularity", taxon="Zea mays")
    )


def test_rejects_vague():
    assert not is_visually_judgeable(
        _t("diversified inflorescence architecture", trait_class="inflorescence")
    )
    assert not is_visually_judgeable(
        _t("highly complex geometrical structures", trait_class="habit")
    )
