import pytest
from app import public_export as pe
from app.models import Category, Generator, GoldPair, ModelOutput, Task


def _mk(db):
    cat = Category(slug="plant", name="Plant")
    g_ok = Generator(slug="lpy", name="L-Py", kind="model")
    g_hidden = Generator(slug="secret", name="Secret", kind="model")
    db.add_all([cat, g_ok, g_hidden])
    db.flush()
    t_pub = Task(category_id=cat.id, title="maize-a", prompt="maize", active=True)
    t_off = Task(category_id=cat.id, title="maize-b", prompt="maize", active=False)
    db.add_all([t_pub, t_off])
    db.flush()
    o_ok = ModelOutput(
        task_id=t_pub.id,
        generator_id=g_ok.id,
        asset_path="a.glb",
        source="external",
        license="CC-BY-4.0",
    )
    o_self = ModelOutput(
        task_id=t_pub.id,
        generator_id=g_ok.id,
        asset_path="b.glb",
        source="bio3d-arena",
        license=None,
    )
    o_bad = ModelOutput(
        task_id=t_pub.id, generator_id=g_ok.id, asset_path="c.glb", source="external", license=None
    )
    db.add_all([o_ok, o_self, o_bad])
    db.flush()
    return locals()


def test_resolve_respects_allowlist_and_active(db_session):
    e = _mk(db_session)
    inc = pe.resolve_include_ids(
        db_session, task_titles=["maize-a", "maize-b"], generator_slugs=["lpy"]
    )
    assert e["t_pub"].id in inc.task_ids
    assert e["t_off"].id not in inc.task_ids  # inactive task excluded
    assert e["g_hidden"].id not in inc.generator_ids  # not in allowlist
    assert e["o_ok"].id in inc.output_ids


def test_check_licenses_fails_loud_on_unknown(db_session):
    e = _mk(db_session)
    inc = pe.resolve_include_ids(db_session, task_titles=["maize-a"], generator_slugs=["lpy"])
    with pytest.raises(pe.LicenseError) as ei:
        pe.check_licenses(db_session, inc.output_ids)
    assert ei.value.output_id == e["o_bad"].id  # external + null license aborts


def test_check_licenses_exempts_self_authored(db_session):
    e = _mk(db_session)
    pe.check_licenses(db_session, {e["o_self"].id})  # bio3d-arena source, no raise


def test_excluded_generators_never_promoted_even_if_passed(db_session):
    """Demeter/Helios are deny-listed: even if a curator lists them in --generators, their
    outputs must not enter the public include set."""
    cat = Category(slug="plant", name="Plant")
    g_demeter = Generator(
        slug="demeter", name="Demeter", kind="model", paradigm="procedural_expert"
    )
    db_session.add_all([cat, g_demeter])
    db_session.flush()
    t = Task(category_id=cat.id, title="maize-a", prompt="maize", active=True)
    db_session.add(t)
    db_session.flush()
    o = ModelOutput(
        task_id=t.id,
        generator_id=g_demeter.id,
        asset_path="d.glb",
        source="external",
        license="CC-BY-4.0",
    )
    db_session.add(o)
    db_session.flush()
    inc = pe.resolve_include_ids(
        db_session, task_titles=["maize-a"], generator_slugs=["demeter", "helios"]
    )
    assert g_demeter.id not in inc.generator_ids
    assert o.id not in inc.output_ids
    assert "demeter" in pe.PUBLIC_EXCLUDED_GENERATORS and "helios" in pe.PUBLIC_EXCLUDED_GENERATORS


def test_resolve_propagates_gold_pair_decoys(db_session):
    """The GoldPair loop in resolve_include_ids is the only real-world path that
    populates IncludeSet.gold_output_ids. A gold pair's good/bad outputs must
    travel with an included task even though they're flagged is_gold (and so are
    excluded from the normal ModelOutput.is_gold split above the loop)."""
    e = _mk(db_session)
    o_good = ModelOutput(
        task_id=e["t_pub"].id,
        generator_id=e["g_ok"].id,
        asset_path="gold-good.glb",
        source="bio3d-arena",
        license=None,
        is_gold=True,
    )
    o_decoy = ModelOutput(
        task_id=e["t_pub"].id,
        generator_id=e["g_ok"].id,
        asset_path="gold-bad.glb",
        source="bio3d-arena",
        license=None,
        is_gold=True,
    )
    db_session.add_all([o_good, o_decoy])
    db_session.flush()
    gp = GoldPair(task_id=e["t_pub"].id, good_output_id=o_good.id, bad_output_id=o_decoy.id)
    db_session.add(gp)
    db_session.flush()

    inc = pe.resolve_include_ids(db_session, task_titles=["maize-a"], generator_slugs=["lpy"])

    assert o_good.id in inc.gold_output_ids
    assert o_decoy.id in inc.gold_output_ids
    assert o_good.id not in inc.output_ids
    assert o_decoy.id not in inc.output_ids
