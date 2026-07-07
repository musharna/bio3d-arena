from app import completeness
from app.organ_inventory import inventory_for


def test_tool_schema_accepts_complement():
    props = completeness.COMPLETENESS_TOOL["input_schema"]["properties"]["organs_present"]["items"][
        "properties"
    ]
    assert set(props["complement"]["enum"]) == {"full", "missing_some", "extra", "uncertain"}


def test_prompt_lists_complement_and_instructs_reporting():
    inv = inventory_for("Canis lupus familiaris")
    text = completeness._build_messages(b"\x89PNG", inv)[0]["content"][0]["text"]
    assert "expect 4" in text  # the dog's legs
    assert "complement" in text.lower()
    assert "do not count exactly" in text.lower() or "not an exact count" in text.lower()
