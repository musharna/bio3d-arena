import pytest
from app import public_export as pe
from app.models import Category, Generator, Task, ModelOutput


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
