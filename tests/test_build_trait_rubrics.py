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


def test_build_rubric_traits_does_not_mutate_source_dicts():
    import scripts.build_trait_rubrics as b

    # A real fetcher may hand back cached/shared dicts; stamping source_tier must
    # not leak back into the caller's object.
    shared = {
        "key": "color",
        "trait_class": "color",
        "type": "categorical",
        "expected": "red",
        "visual": True,
        "citation": "POWO",
    }
    out = b.build_rubric_traits("X", fetch_db=lambda _t: [shared], draft_llm=lambda _t: [])
    assert "source_tier" not in shared  # source dict untouched
    assert out[0]["source_tier"] == "db"  # the copy got stamped


def test_build_rubric_traits_merges_dedups_and_verifies():
    import scripts.build_trait_rubrics as b

    def fetch_db(_t):
        return [
            {
                "key": "wd_flower_color",
                "trait_class": "color",
                "type": "categorical",
                "expected": "red",
                "visual": True,
                "citation": "https://www.wikidata.org/wiki/Q1",
                "source_detail": "Q1",
                "quote": "P2827=red",
            }
        ]

    def draft_llm(_t):
        return [
            # same (color, red) as db → deduped out (db preferred)
            {
                "key": "petal_red",
                "trait_class": "color",
                "type": "categorical",
                "expected": "red",
                "visual": True,
                "citation": "10.1/a",
                "source_detail": "10.1/a",
                "quote": "red corolla",
            },
            # distinct trait → kept
            {
                "key": "leaf_shape",
                "trait_class": "organ_shape",
                "type": "categorical",
                "expected": "compound",
                "visual": True,
                "citation": "10.1/b",
                "source_detail": "10.1/b",
                "quote": "compound leaves",
            },
        ]

    # verify_fn drops the leaf citation 10.1/b → only the db color trait survives
    def verify_fn(traits):
        return [t for t in traits if t["citation"] != "10.1/b"]

    out = b.build_rubric_traits("X", fetch_db=fetch_db, draft_llm=draft_llm, verify_fn=verify_fn)
    keys = {t["key"] for t in out}
    assert keys == {"wd_flower_color"}  # llm color deduped, llm leaf verify-dropped
    assert out[0]["source_tier"] == "db"


def test_merge_dedup_key_collision_suffix():
    """Two traits with different (trait_class, expected) sigs but the same key:
    both survive; the second gets key_2."""
    import scripts.build_trait_rubrics as b

    t1 = {
        "key": "petal_trait",
        "trait_class": "color",
        "type": "categorical",
        "expected": "red",
        "visual": True,
        "source_tier": "db",
        "citation": "POWO",
    }
    t2 = {
        "key": "petal_trait",  # same key, distinct (trait_class, expected)
        "trait_class": "organ_shape",
        "type": "categorical",
        "expected": "obovate",
        "visual": True,
        "source_tier": "llm",
        "citation": "10.1/x",
    }
    out = b._merge_dedup([t1, t2])
    assert len(out) == 2, f"expected 2 traits, got {len(out)}"
    keys = [t["key"] for t in out]
    assert "petal_trait" in keys, "first trait should keep original key"
    assert "petal_trait_2" in keys, "second trait should get _2 suffix"


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


def test_dry_run_reports_counts_without_spend(capsys, monkeypatch):
    import scripts.build_trait_rubrics as b

    # Stub the network helpers so dry-run does zero real I/O and zero spend.
    monkeypatch.setattr(
        b, "_live_wikidata_sparql", lambda taxon: {"qid": "Q1", "props": {"P2827": "red"}}
    )
    monkeypatch.setattr(
        b,
        "_live_lit_search",
        lambda taxon: [{"doi": "10.1/a", "abstractText": "x"}, {"doi": "10.1/b"}],
    )
    monkeypatch.setattr(b, "_live_lit_resolve", lambda pub: pub.get("abstractText"))

    rc = b.dry_run_report(["Solanum lycopersicum"])
    captured = capsys.readouterr().out
    assert rc == 0
    assert "Solanum lycopersicum" in captured
    assert "db traits=1" in captured  # P2827 mapped
    assert "candidate pubs=2" in captured
    assert "OA-resolvable=1" in captured  # only 10.1/a has text
    assert "est. LLM calls=1" in captured
