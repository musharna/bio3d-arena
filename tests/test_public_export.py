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


def test_is_own_output_covers_every_generation_harness():
    """Ownership is a PROPERTY OF OUR PIPELINE, not one magic source string. Every harness we
    run — the legacy `bio3d-arena` label, LLM code-gen (`commissioned`), the render-critique
    loop (`agentic:<model>`), and our authored L-systems (`procedural:<gen>`) — produces an
    artifact we hold rights to, so none of them needs a third-party redistribution license.
    Third-party assets and commercial model APIs are NOT ours and must still be gated."""
    for src in (
        "bio3d-arena",
        "commissioned",
        "agentic:anthropic/claude-opus-4.8",
        "procedural:lpy",
    ):
        assert pe.is_own_output(src) is True, src
    for src in ("api:meshy", "recon:trellis", "frontier:hunyuan", "objaverse", "crops3d", None, ""):
        assert pe.is_own_output(src) is False, src


def test_check_licenses_exempts_our_llm_generated_outputs(db_session):
    """The 441-output deploy-gate blocker: our own commissioned/agentic outputs carry no
    license string (nothing grants us rights to our own work), so the gate must not treat a
    NULL license on OUR artifact as a rights failure."""
    e = _mk(db_session)
    ours = [
        ModelOutput(
            task_id=e["t_pub"].id,
            generator_id=e["g_ok"].id,
            asset_path=f"{slug}.glb",
            source=slug,
            license=None,
        )
        for slug in ("commissioned", "agentic:x-ai/grok-4.5", "procedural:lpy")
    ]
    db_session.add_all(ours)
    db_session.flush()
    pe.check_licenses(db_session, {o.id for o in ours})  # must not raise


def test_display_posture_keeps_our_unlabeled_generation(db_session):
    """filter_include_for_posture must use the same ownership predicate as check_licenses —
    otherwise the gate and the filter disagree about what 'ours' means."""
    e = _mk(db_session)
    mine = ModelOutput(
        task_id=e["t_pub"].id,
        generator_id=e["g_ok"].id,
        asset_path="mine.glb",
        source="commissioned",
        license=None,
    )
    theirs = ModelOutput(
        task_id=e["t_pub"].id,
        generator_id=e["g_ok"].id,
        asset_path="theirs.glb",
        source="objaverse",
        license=None,
    )
    db_session.add_all([mine, theirs])
    db_session.flush()

    for posture in ("display", "redistribute"):
        inc = pe.IncludeSet(output_ids={mine.id, theirs.id})
        pe.filter_include_for_posture(db_session, inc, posture, set())
        assert mine.id in inc.output_ids, posture
        assert theirs.id not in inc.output_ids, posture  # unlabeled third-party still gated


def test_gold_posture_uses_the_same_ownership_predicate(db_session):
    """The gold path carries its own copy of the ownership test. If it keeps the old literal,
    a gold output aliasing one of our commissioned assets is silently dropped from the bundle."""
    e = _mk(db_session)
    real = ModelOutput(
        task_id=e["t_pub"].id,
        generator_id=e["g_ok"].id,
        asset_path="shared.glb",
        source="commissioned",
        license=None,
    )
    db_session.add(real)
    db_session.flush()
    gold = ModelOutput(
        task_id=e["t_pub"].id,
        generator_id=e["g_ok"].id,
        asset_path="shared.glb",  # aliases the commissioned asset above
        source="calibration",
        license=None,
        is_gold=True,
    )
    db_session.add(gold)
    db_session.flush()

    inc = pe.IncludeSet(gold_output_ids={gold.id})
    pe.filter_gold_for_posture(db_session, inc, "display", set())
    assert gold.id in inc.gold_output_ids


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
