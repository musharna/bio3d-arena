# tests/test_semantic_generalization.py
from app import semantic


def test_reject_codes_generalized():
    assert "not_the_organism" in semantic.REJECT_CODES
    assert "not_a_plant" not in semantic.REJECT_CODES
    assert semantic.SEMANTIC_TOOL["input_schema"]["properties"]["verdict"]["enum"] == [
        "ok",
        "multiple",
        "sub_part",
        "not_the_organism",
        "uncertain",
    ]
    assert "plant" not in semantic.SEMANTIC_TOOL["description"].lower()


def test_prompt_is_taxon_parameterized_no_plant():
    msg = semantic._build_messages(b"\x89PNG", "Boletus edulis")
    text = msg[0]["content"][0]["text"]
    assert "Boletus edulis" in text
    assert "plant" not in text.lower()  # generalized to organism


def test_prompt_colonial_clause_only_for_colonial_taxa():
    colonial = semantic._build_messages(b"\x89PNG", "Trametes versicolor")[0]["content"][0]["text"]
    unitary = semantic._build_messages(b"\x89PNG", "Boletus edulis")[0]["content"][0]["text"]
    assert "cluster of the same species" in colonial.lower()
    assert "cluster of the same species" not in unitary.lower()


def test_verdict_not_the_organism_rejects():
    v = semantic.verdict_from_code("not_the_organism", "a blob")
    assert v.admit is False and v.reason == "not_the_organism"


def test_prompt_is_admissibility_not_fidelity():
    # The gate must NOT re-do the dropped wrong_species / fidelity judgment: a low-fidelity but
    # recognizable organism is admissible; not_the_organism is for junk/blobs, not poor renders.
    text = semantic._build_messages(b"\x89PNG", "Hericium erinaceus")[0]["content"][0]["text"]
    low = text.lower()
    assert "admissibility" in low
    assert "voters judge fidelity" in low
    assert "low-fidelity" in low and "admissible" in low
    # not_the_organism must be framed as junk, and explicitly NOT a poor/inaccurate depiction
    assert "junk" in low or "blob" in low
    assert "poor or inaccurate depiction" in low
