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


def _registry() -> dict[str, Predicate]:
    # Lazy (function-local) import of StructuralPredicate avoids a module-level import cycle
    # (structural.py imports Verdict from this module). Guarded: structural.py doesn't exist
    # until Task 4, so absence of the module is fine as long as it's never in the active rubric.
    reg: dict[str, Predicate] = {"completeness": CompletenessPredicate()}
    try:
        from .structural import StructuralPredicate  # added in Task 4

        reg["structural"] = StructuralPredicate()
    except ImportError:
        pass
    return reg


def non_admitted_output_ids(db: Session, rubric: list[str] | None = None) -> set[int]:
    """Union of rejected ids across the rubric's predicates. rubric=None -> DEFAULT_RUBRIC.
    Unknown predicate name -> KeyError (fail-loud)."""
    reg = _registry()
    names = DEFAULT_RUBRIC if rubric is None else rubric
    out: set[int] = set()
    for name in names:
        out |= reg[name].rejected_output_ids(db)
    return out
