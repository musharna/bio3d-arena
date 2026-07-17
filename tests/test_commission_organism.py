# tests/test_commission_organism.py
"""The code-gen paradigms (procedural_llm, agentic) must reach every kingdom, not just plants.

The bug: commission.SPECIES_COMMON was a literal 6-PLANT dict and resolve_taxon_tasks() iterated
only it, so the 7 fungi and 4 animal tasks were unreachable — procedural_llm and agentic had 0
outputs on every fungus and every animal, and their leaderboards rendered empty under a Fungi or
Animals filter. The prompts were plant-shaped too ("botanically accurate ... whole {common} plant",
"stem/trunk, leaves"; agentic's critique asked about "leaf/needle shape").

The fix is to drive the roster AND the body-plan requirements from ORGAN_INVENTORY — the same
registry the completeness metric already scores against — so a fungus is asked for a cap on a
stalk and a goldfish for its PAIRED pectoral fins.
"""

import pytest

from app import commission
from app.agentic import critique_prompt
from app.commission import build_prompt, common_name, resolve_taxon_tasks
from app.database import SessionLocal, init_db
from app.models import Category, Task, TraitRubric
from app.organ_inventory import ORGAN_INVENTORY

PLANT_WORDS = ("plant", "leaf", "leaves", "stem", "trunk", "needle", "botanical")


def setup_module(_m):
    init_db()


def test_every_inventory_taxon_has_a_common_name():
    """The roster is derived from ORGAN_INVENTORY, so a taxon there without a common name would
    be a taxon the generator can see but cannot name. Fail loud at the registry, not at runtime."""
    missing = [t for t in ORGAN_INVENTORY if t not in commission.SPECIES_COMMON]
    assert missing == []


def test_common_name_is_fail_loud_for_an_unknown_taxon():
    with pytest.raises(KeyError, match="Nonexistent species"):
        common_name("Nonexistent species")


def test_roster_reaches_fungi_and_animals(tmp_path):
    """resolve_taxon_tasks must return fungus and animal tasks, not only the 6 plants."""
    with SessionLocal() as db:
        cat = Category(slug="organism-roster", name="Organism roster")
        db.add(cat)
        db.flush()
        found = {}
        for taxon in ("Amanita muscaria", "Carassius auratus", "Solanum lycopersicum"):
            task = Task(category_id=cat.id, title=taxon, prompt="p")
            db.add(task)
            db.flush()
            db.add(TraitRubric(task_id=task.id, taxon=taxon))
            found[taxon] = task.id
        db.flush()

        pairs = dict(resolve_taxon_tasks(db))

        assert pairs.get("Amanita muscaria") == found["Amanita muscaria"], "fungus unreachable"
        assert pairs.get("Carassius auratus") == found["Carassius auratus"], "animal unreachable"
        assert pairs.get("Solanum lycopersicum") == found["Solanum lycopersicum"]
        db.rollback()


def test_fungus_prompt_asks_for_a_cap_not_a_stem():
    prompt = build_prompt("Amanita muscaria", "fly agaric").lower()

    assert "cap" in prompt
    assert "fly agaric" in prompt and "amanita muscaria" in prompt
    for word in PLANT_WORDS:
        assert word not in prompt, f"fungus prompt still says {word!r}"


def test_animal_prompt_carries_the_paired_complement():
    """The goldfish inventory says pectoral_fin has complement=2 — a body plan the prompt must
    state so the generator builds the full paired complement. (The completeness metric no longer
    gates a category on the VLM's limb COUNT — too noisy from a turntable sheet, see
    completeness.derive — but the correct body plan is still the generation target.)"""
    prompt = build_prompt("Carassius auratus", "goldfish")

    assert "goldfish" in prompt.lower()
    assert "2" in prompt and "pectoral" in prompt.lower()
    for word in PLANT_WORDS:
        assert word not in prompt.lower(), f"animal prompt still says {word!r}"


def test_plant_prompt_still_names_plant_organs():
    """Generalizing must not lobotomize the plants: a tomato still needs its plant body plan."""
    prompt = build_prompt("Solanum lycopersicum", "tomato").lower()

    assert "tomato" in prompt
    assert "leaf" in prompt or "leaves" in prompt or "stem" in prompt


def test_prompt_keeps_the_blender_runtime_contract():
    """The bpy runtime contract is what makes the script executable at all — it must survive.

    The export is NOT part of that contract any more. This test used to assert `OUT_GLB in prompt`;
    that clause is what made the model reproduce our GLB export call from memory, and 14% of the
    first sweep's failures died on it with the organism already built. The harness owns the export
    now, so the runtime contract is only: the right Blender, running headless."""
    prompt = build_prompt("Amanita muscaria", "fly agaric")

    assert "OUT_GLB" not in prompt
    assert "Blender 4.2" in prompt
    assert "--background" in prompt


def test_agentic_critique_is_organism_neutral():
    fungus = critique_prompt("Amanita muscaria", "fly agaric").lower()
    fish = critique_prompt("Carassius auratus", "goldfish").lower()

    for word in PLANT_WORDS:
        assert word not in fungus, f"fungus critique still says {word!r}"
        assert word not in fish, f"fish critique still says {word!r}"
    assert "cap" in fungus
    assert "fin" in fish
