# tests/test_rosters.py
"""The agentic roster must be vision-capable, and both rosters must be lab-diverse.

Why this is tested rather than trusted: the agentic loop feeds the model a RENDER of its own mesh
and asks it to critique it. A text-only entrant fails that call — and run_bpy/agentic record a
failed attempt AGAINST THE MODEL, which /procedural turns into pass@1. So a mis-rostered text-only
model would be published as a model that cannot build a mushroom. The roster is scoring input.
"""

from app.rosters import (
    AGENTIC_ROSTER,
    PROCEDURAL_ROSTER,
    TEXT_ONLY,
    is_agentic_eligible,
)


def test_no_text_only_model_is_in_the_agentic_roster():
    offenders = [m for m in AGENTIC_ROSTER if not is_agentic_eligible(m)]
    assert offenders == [], f"text-only model(s) in the agentic roster: {offenders}"


def test_procedural_is_a_superset_of_agentic():
    """Procedural has no vision constraint, so it fields everything agentic does, plus the
    text-only coders. A model in agentic but not procedural would be an oversight."""
    assert set(AGENTIC_ROSTER) <= set(PROCEDURAL_ROSTER)


def test_procedural_fields_the_text_only_coders_agentic_cannot():
    assert TEXT_ONLY <= set(PROCEDURAL_ROSTER)
    assert not (TEXT_ONLY & set(AGENTIC_ROSTER))


def test_rosters_have_no_duplicates():
    assert len(AGENTIC_ROSTER) == len(set(AGENTIC_ROSTER))
    assert len(PROCEDURAL_ROSTER) == len(set(PROCEDURAL_ROSTER))


def test_roster_is_lab_diverse_not_one_lab_restyled():
    """Task #78 asked for a DIVERSE roster. Eight entrants from two labs is not diversity — the
    whole point is that the board separates approaches, not checkpoints of one family."""
    labs = {m.split("/", 1)[0] for m in AGENTIC_ROSTER}
    assert len(labs) >= 6, f"agentic roster spans only {len(labs)} labs: {sorted(labs)}"
    assert len(AGENTIC_ROSTER) >= 6, "agentic was the thinnest board — it needs >= 6 entrants"
