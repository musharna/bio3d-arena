# tests/test_build_trait_rubrics.py
from __future__ import annotations

import json

from app.database import SessionLocal, init_db
from app.models import TraitRubric


def setup_module(_m):
    init_db()


def test_validate_rejects_uncited_and_bad_class():
    import scripts.build_trait_rubrics as b

    b.validate_trait(
        {
            "key": "k",
            "trait_class": "color",
            "type": "categorical",
            "expected": "red",
            "visual": True,
            "source_tier": "db",
            "citation": "POWO",
        }
    )  # ok
    for bad in [
        {
            "key": "k",
            "trait_class": "height",
            "type": "x",
            "expected": "2m",
            "visual": True,
            "source_tier": "db",
            "citation": "POWO",
        },  # bad class
        {
            "key": "k",
            "trait_class": "color",
            "type": "categorical",
            "expected": "red",
            "visual": True,
            "source_tier": "db",
            "citation": "",
        },  # empty citation
        {
            "key": "k",
            "trait_class": "color",
            "type": "categorical",
            "expected": "red",
            "visual": True,
            "source_tier": "guess",
            "citation": "x",
        },  # bad tier
        {
            "key": "k",
            "trait_class": "color",
            "type": "categorical",
            "expected": "red",
            "visual": True,
            "source_tier": "db",
            "citation": None,
        },  # None citation
        {
            "key": "k",
            "trait_class": "color",
            "type": "categorical",
            "expected": "red",
            "visual": True,
            "source_tier": "db",
        },  # missing citation key
    ]:
        try:
            b.validate_trait(bad)
            assert False, f"expected ValueError for {bad}"
        except ValueError:
            pass


def test_upsert_rubric_persists_validated_traits():
    import scripts.build_trait_rubrics as b

    with SessionLocal() as db:
        db.query(TraitRubric).filter_by(taxon="Test taxon").delete(False)
        db.commit()
        traits = [
            {
                "key": "habit",
                "trait_class": "habit",
                "type": "categorical",
                "expected": "herb",
                "visual": True,
                "source_tier": "llm",
                "citation": "Flora 2026",
            }
        ]
        r = b.upsert_rubric(db, "Test taxon", None, traits)
        assert json.loads(db.get(TraitRubric, r.id).traits_json)[0]["key"] == "habit"
