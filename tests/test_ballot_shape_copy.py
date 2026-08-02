"""Visitor-facing copy must not hard-code how many models are on a ballot.

`arena.html` states the rule in a comment: a ballot is four models where the task can fill a
quad and two where it cannot, so any fixed count is wrong half the time. The rule was followed
on the arena hero and nowhere else — when k-wise became the default (PR #121) the homepage still
opened with "Two AI reconstructions ... judged side by side", the skip button still said "skip
this pair" beneath four models, and the footer still called it a pairwise comparison. A cold
visitor read a promise the product no longer kept, and nothing failed.

This guards the rule mechanically, because the failure mode is silent: the copy stays valid
English and every test still passes.

**Scope is deliberate.** `methodology.html` is EXCLUDED. Its "pairwise" language is correct and
must stay — Bradley-Terry genuinely fits pairwise decompositions, and a pick-best-of-four writes
three pairwise rows (verified against production: one k-wise submission, three `vote` rows). The
distinction this file encodes is that the STATISTICS are pairwise while the INTERACTION is not.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "templates"

#: Templates a first-time visitor reads before or while voting. `methodology.html` is absent on
#: purpose — see the module docstring.
VISITOR_FACING = ["home.html", "arena.html", "base.html"]

#: Phrases that assert a ballot size. Each is paired with what to say instead, so a failure
#: tells the next person what the fix is rather than only that they broke a rule.
BANNED = [
    (r"\bskip this pair\b", 'say "skip this comparison"'),
    (r"\bNext pair\b", 'say "Next comparison"'),
    (r"\bTwo AI reconstructions\b", 'drop the count: "AI reconstructions ..."'),
    (r"\bpairwise comparison\b", 'describing the interaction: say "blind comparison"'),
    (r"\bboth models\b", 'a ballot may hold four: say "the models"'),
    (r"\beither model\b", 'a ballot may hold four: say "any model"'),
]


def _strip_jinja_comments(text: str) -> str:
    """Drop `{# ... #}` blocks.

    Without this the guard fires on its own explanatory comments — several of which quote the
    banned strings verbatim to record what they used to say. A comment is not visitor-facing.
    """
    return re.sub(r"\{#.*?#\}", "", text, flags=re.DOTALL)


@pytest.mark.parametrize("name", VISITOR_FACING)
def test_no_hardcoded_ballot_size_in_visitor_copy(name):
    body = _strip_jinja_comments((TEMPLATES / name).read_text())
    for pattern, remedy in BANNED:
        found = re.search(pattern, body, flags=re.IGNORECASE)
        assert not found, (
            f"{name} contains {found.group(0)!r}, which asserts a ballot size. "
            f"A ballot is four models or two depending on whether the task fills a quad, so "
            f"this is wrong for half of all ballots. Instead: {remedy}."
        )


def test_methodology_keeps_its_pairwise_language():
    """The positive control: this guard must not be 'fixed' by purging the word everywhere.

    Without this, someone could satisfy the test above by rewriting methodology.html too, which
    would make the statistical description WRONG. A rule that only forbids is a rule that gets
    over-applied.
    """
    body = (TEMPLATES / "methodology.html").read_text()
    assert "pairwise" in body, (
        "methodology.html lost its pairwise language. Bradley-Terry is fitted on pairwise "
        "comparisons — that description is correct and is not what the ballot-shape rule is "
        "about. Revert it."
    )
