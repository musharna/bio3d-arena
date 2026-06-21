"""Subject Spotlight: deterministic failure-flag derivation + page-data assembly.

A Spotlight is a curated deep-dive on one benchmark subject, showing every model we
have for it with all metrics, failure flags, and (Phase 2) critic notes. Internal
inspection tool — see docs/superpowers/specs/2026-06-21-subject-spotlight-design.md.
"""

from __future__ import annotations

from .models import Metric

# Tunable thresholds (initial; see spec §Components).
COVERAGE_MIN = 0.5
FSCORE_MIN = 0.5


def derive_flags(metric: Metric | None) -> list[tuple[str, str]]:
    """Deterministic failure/ok flags from a Metric. Each flag is (kind, label);
    kind drives a CSS severity class. Never raises."""
    if metric is None or metric.status != "ok" or metric.chamfer is None:
        return [("unscored", "no objective score")]
    flags: list[tuple[str, str]] = []
    lo, hi, ch = metric.gt_band_lo, metric.gt_band_hi, metric.chamfer
    if hi is not None and ch > hi:
        flags.append(("shape", "outside natural variation"))
    elif lo is not None and hi is not None and lo <= ch <= hi:
        flags.append(("ok", "within natural variation"))
    if metric.coverage is not None and metric.coverage < COVERAGE_MIN:
        flags.append(("coverage", "missing geometry"))
    if metric.fscore is not None and metric.fscore < FSCORE_MIN:
        flags.append(("surface", "low F-score@τ"))
    return flags or [("ok", "scored")]
