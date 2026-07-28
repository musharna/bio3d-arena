"""Every cached board the public site renders must travel in the bundle.

Found on the live public instance, 2026-07-28: the AI-judge board rendered its empty state —
"No automated LLM-judge rankings for this selection yet" — on a project that has judged four
paradigms. `Rating` was the only board in EXPORT_MODELS, so the global human leaderboard shipped
and the kingdom and judge boards had no cache to read.

The judge boards are the ones that cannot be recovered on the far side. A human rating is at
least reproducible from the votes beside it, but `judge_vote` is deliberately NOT exported (a
judge ballot references outputs the posture filter may drop, so shipping ballots would dangle).
With neither the ballots nor the fitted ratings, a public judge board is blank forever.

The second test is the containment: a promoted rating must never outlive the generator it ranks.
"""

from __future__ import annotations

from scripts.export_public import EXPORT_MODELS

_EXPORTED = {m.__tablename__ for m in EXPORT_MODELS}


def test_all_four_cached_boards_travel():
    for table in ("rating", "kingdom_rating", "judge_rating", "kingdom_judge_rating"):
        assert table in _EXPORTED, f"{table} is rendered by a public route but never exported"


def test_the_judge_ballots_themselves_stay_internal():
    """The ratings ship; the ballots do not. A judge_vote references outputs the posture filter
    may have dropped, so exporting the raw ballots would dangle on import."""
    assert "judge_vote" not in _EXPORTED


def test_judge_boards_ship_but_only_for_generators_that_ship(db_session, tmp_path):
    """The behaviour the fix has to produce: a judge rating for an included generator travels,
    and one for a generator the allowlist excluded does not — otherwise the bundle carries a
    dangling generator_id and ranks an entrant the public site never shows."""
    import json

    from app.models import Criterion, Generator, JudgeRating, KingdomJudgeRating, KingdomRating
    from app.storage import LocalStorageBackend
    from scripts.export_public import export_bundle
    from tests.factories import cascade_delete
    from tests.test_public_export import _mk

    # Same cross-file leak guard the neighbouring export tests use.
    cascade_delete(db_session, Generator, Generator.slug.in_(["lpy", "calibration"]))
    db_session.flush()

    e = _mk(db_session)
    e["o_bad"].license = "CC-BY-4.0"  # the license gate is not what this test is about
    crit = Criterion(slug="overall", name="Overall")
    db_session.add(crit)
    db_session.flush()

    kept = e["g_ok"].id  # slug "lpy" — in the allowlist below
    dropped = e["g_hidden"].id  # slug "secret" — deliberately not
    db_session.add_all(
        [
            JudgeRating(
                generator_id=kept, criterion_id=crit.id, view_condition="multi", bt_score=1200.0
            ),
            JudgeRating(
                generator_id=dropped, criterion_id=crit.id, view_condition="multi", bt_score=900.0
            ),
            KingdomJudgeRating(
                generator_id=kept,
                kingdom="plants",
                criterion_id=crit.id,
                view_condition="multi",
                bt_score=1100.0,
            ),
            KingdomRating(
                generator_id=kept, kingdom="plants", criterion_id=crit.id, bt_score=1050.0
            ),
        ]
    )
    db_session.flush()

    store = LocalStorageBackend(tmp_path / "src")
    for k in ("a.glb", "b.glb", "c.glb"):
        store.save(k, b"GLB")
    out = tmp_path / "bundle"
    export_bundle(db_session, store, task_titles=["maize-a"], generator_slugs=["lpy"], out_dir=out)
    rows = json.loads((out / "rows.json").read_text())

    assert [r["generator_id"] for r in rows["judge_rating"]] == [kept]
    assert [r["generator_id"] for r in rows["kingdom_judge_rating"]] == [kept]
    assert [r["generator_id"] for r in rows["kingdom_rating"]] == [kept]
