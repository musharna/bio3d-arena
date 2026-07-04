# app/admissibility.py
"""Pre-vote admissibility gate: an output is admitted iff every predicate in the active rubric
admits it. Predicates are pluggable (structural geometry, completeness category, ...); the pool
gate calls non_admitted_output_ids() — the single composer. Generalizes by swapping the rubric
(a list of predicate names), machinery unchanged."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy.orm import Session

from . import config, flags


@dataclass(frozen=True)
class Verdict:
    admit: bool
    reason: str = ""  # "" when admit; else a short machine code, e.g. "degenerate_bbox"
    detail: dict = field(default_factory=dict)


class Predicate(Protocol):
    name: str
    version: str

    def rejected_output_ids(self, db: Session) -> set[int]:
        """Output ids this predicate does NOT admit (precomputed source; keeps the gate O(1))."""
        ...


class CompletenessPredicate:
    name = "completeness"
    version = "completeness-v1"

    def rejected_output_ids(self, db: Session) -> set[int]:
        # Reuse the existing completeness-category exclusion verbatim (no re-scoring, no drift).
        return flags.excluded_output_ids_by_completeness(
            db, config.POOL_EXCLUDED_COMPLETENESS_CATEGORIES
        )


DEFAULT_RUBRIC: list[str] = ["structural", "completeness"]


def _effective_rubric() -> list[str]:
    """The default rubric, plus 'semantic' iff the semantic predicate is in gate mode. DEFAULT_RUBRIC
    stays static (advisory/off never auto-exclude); the composer only reaches the semantic predicate
    for the default (rubric=None) call, which is the live pool gate. Explicit rubric= calls are used
    verbatim, unaffected by the mode."""
    rubric = list(DEFAULT_RUBRIC)
    if config.SEMANTIC_ADMISSIBILITY_MODE == "gate":
        rubric.append("semantic")
    return rubric


def _registry() -> dict[str, Predicate]:
    # Function-local imports: structural.py and semantic.py import Verdict from this module, so a
    # module-level import here would be a real circular import. Direct (unguarded) — a genuine
    # ImportError must fail loud, not degrade to "predicate absent".
    from .semantic import SemanticPredicate
    from .structural import StructuralPredicate

    return {
        "completeness": CompletenessPredicate(),
        "structural": StructuralPredicate(),
        "semantic": SemanticPredicate(),
    }


def non_admitted_output_ids(db: Session, rubric: list[str] | None = None) -> set[int]:
    """Union of rejected ids across the rubric's predicates. rubric=None -> the mode-aware effective
    rubric (DEFAULT_RUBRIC + semantic-if-gate). Unknown predicate name -> KeyError (fail-loud)."""
    reg = _registry()
    names = _effective_rubric() if rubric is None else rubric
    out: set[int] = set()
    for name in names:
        out |= reg[name].rejected_output_ids(db)
    return out
