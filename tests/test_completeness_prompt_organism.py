from app import completeness
from app.organ_inventory import inventory_for


def test_completeness_prompt_says_organism_not_plant():
    inv = inventory_for("Boletus edulis")
    assert inv is not None
    msg = completeness._build_messages(b"\x89PNG", inv)
    text = (
        msg[0]["content"][1]["text"]
        if msg[0]["content"][1]["type"] == "text"
        else msg[0]["content"][0]["text"]
    )
    assert "Boletus edulis" in text
    assert "plant" not in text.lower()
