"""Curated promotion boundary for the public instance (SP1).

Resolves the exact row-id sets that may be published, and enforces the license gate.
Pure DB reads; no filesystem, no serialization (that's scripts/export_public.py).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

# Defined in app.licensing (one allowlist for outputs AND reference photos); imported here rather
# than re-declared, and still importable from this module, which was its home.
from .licensing import REDISTRIBUTABLE_LICENSES
from .models import Generator, GoldPair, ModelOutput, Task

logger = logging.getLogger(__name__)

# Generators never promoted to the public instance, regardless of the curator's --generators
# allowlist. Demeter + Helios are internal procedural_expert testers (agrigen) whose outputs are
# inconsistent on the public arena (some render untextured/colorless); kept internal-only. A
# deny-list here makes the exclusion durable — an operator can't accidentally publish them.
PUBLIC_EXCLUDED_GENERATORS = frozenset({"agrigen", "demeter", "helios"})


# Sources never promoted to a public bundle, in EITHER posture (checked before the posture
# predicate). found:xfrog = licensed commercial botanical assets; frontier:partcrafter is a
# frontier: commercial model that would otherwise survive the display posture — both are
# internal-data-only (see config.APP_HIDDEN_SOURCES, which hides them from the app UI too).
HARD_EXCLUDE_SOURCES = frozenset(
    {"found:xfrog", "frontier:partcrafter", "procedural:demeter", "procedural:agrigen"}
)
_COMMERCIAL_MODEL_PREFIXES = ("api:", "recon:", "frontier:")

# Sources our OWN pipeline produces. Ownership is a property of the harness that made the
# artifact, not a permission someone granted us — so our own work has no third-party license
# string to carry, and a NULL license on these rows is correct rather than a gap.
#
# This set exists because ownership used to be spelled as the single literal
# `source == "bio3d-arena"` in three separate places, while our generators actually write
# `commissioned` / `agentic:<model>` / `procedural:<gen>`. Equally-ours work therefore missed
# the exemption and was gated as if it were an unlicensed third-party asset (441 outputs).
# Adding a source here is the ONE place that decision now lives.
#
# Orthogonal to exclusion: `procedural:demeter` / `procedural:agrigen` are also ours, and are
# kept out of public bundles by HARD_EXCLUDE_SOURCES for quality reasons — not rights ones.
_OWN_OUTPUT_SOURCES = frozenset({"bio3d-arena", "commissioned"})
_OWN_OUTPUT_PREFIXES = ("agentic:", "procedural:")


def is_commercial_model(source: str | None) -> bool:
    return (source or "").startswith(_COMMERCIAL_MODEL_PREFIXES)


def is_own_output(source: str | None) -> bool:
    """True iff WE produced this artifact, so no redistribution license is required for it.

    Covers every generation harness we run: the legacy `bio3d-arena` label, LLM code-gen
    (`commissioned`), the render-critique-revise loop (`agentic:<model>`), and our authored
    L-systems (`procedural:<gen>`). Third-party assets (objaverse, crops3d, sketchfab, …) and
    commercial model APIs (`api:`/`recon:`/`frontier:`) are NOT ours and stay gated."""
    s = source or ""
    return s in _OWN_OUTPUT_SOURCES or s.startswith(_OWN_OUTPUT_PREFIXES)


def effective_provenance(db: Session, o: ModelOutput) -> tuple[str | None, str | None]:
    """True (source, license) for gating: for a gold output, that of the non-gold
    ModelOutput sharing its asset_path (the real asset it silently aliases) if one
    exists, else its own; for a non-gold output, always its own."""
    if o.is_gold:
        alias = (
            db.execute(
                select(ModelOutput).where(
                    ModelOutput.asset_path == o.asset_path,
                    ModelOutput.is_gold.is_(False),
                )
            )
            .scalars()
            .first()
        )
        if alias is not None:
            return alias.source, alias.license
    return o.source, o.license


class LicenseError(RuntimeError):
    def __init__(self, output_id: int, license_: str | None):
        self.output_id = output_id
        self.license = license_
        super().__init__(f"output {output_id}: non-redistributable license {license_!r}")


@dataclass
class IncludeSet:
    generator_ids: set[int] = field(default_factory=set)
    task_ids: set[int] = field(default_factory=set)
    output_ids: set[int] = field(default_factory=set)
    gold_output_ids: set[int] = field(default_factory=set)


def resolve_include_ids(
    db: Session, *, task_titles: list[str], generator_slugs: list[str]
) -> IncludeSet:
    from .service import app_hidden_generator_ids

    inc = IncludeSet()
    # Anything hidden from the app UI everywhere (internal-only — retrieval + procedural_expert
    # paradigms, xfrog/partcrafter sources, AgriGen testers, the pruned self-hosted recon dups)
    # must never enter the public redistribute dataset either, regardless of the curator allowlist.
    hidden = app_hidden_generator_ids(db)
    allowed = [s for s in generator_slugs if s not in PUBLIC_EXCLUDED_GENERATORS]
    inc.generator_ids = {
        g.id
        for g in db.execute(select(Generator).where(Generator.slug.in_(allowed))).scalars()
        if g.slug not in PUBLIC_EXCLUDED_GENERATORS and g.id not in hidden
    }
    inc.task_ids = {
        t.id
        for t in db.execute(
            select(Task).where(Task.title.in_(task_titles), Task.active.is_(True))
        ).scalars()
    }
    rows = (
        db.execute(
            select(ModelOutput).where(
                ModelOutput.task_id.in_(inc.task_ids),
                ModelOutput.generator_id.in_(inc.generator_ids),
            )
        )
        .scalars()
        .all()
    )
    for o in rows:
        (inc.gold_output_ids if o.is_gold else inc.output_ids).add(o.id)
    # Gold decoys referenced by GoldPairs on included tasks travel too (integrity checks).
    for gp in db.execute(select(GoldPair)).scalars():
        for oid in (gp.good_output_id, gp.bad_output_id):
            o = db.get(ModelOutput, oid)
            if o and o.task_id in inc.task_ids:
                inc.gold_output_ids.add(oid)
    return inc


def filter_include_for_posture(
    db: Session, inc: "IncludeSet", posture: str, gated: set[int]
) -> None:
    """Narrow inc.output_ids in place per posture. redistribute = strict redistributable, no
    commercial-model. display = redistributable OR commercial-model recon. Both drop the hard-
    excludes and admissibility-gated. Attribution/labeling is carried by the row export."""
    from .licensing import normalize_license

    keep: set[int] = set()
    for oid in inc.output_ids:
        if oid in gated:
            continue
        o = db.get(ModelOutput, oid)
        if o is None or o.source in HARD_EXCLUDE_SOURCES:
            continue
        redistributable = is_own_output(o.source) or (
            normalize_license(o.license) in REDISTRIBUTABLE_LICENSES
        )
        if posture == "redistribute":
            if redistributable and not is_commercial_model(o.source):
                keep.add(oid)
        elif posture == "display":
            if redistributable or is_commercial_model(o.source):
                keep.add(oid)
        else:
            raise ValueError(f"unknown posture {posture!r}")
    dropped = inc.output_ids - keep
    if dropped:
        logger.info("posture %s dropped %d output ids: %s", posture, len(dropped), sorted(dropped))
    inc.output_ids = keep


def filter_gold_for_posture(db: Session, inc: "IncludeSet", posture: str, gated: set[int]) -> None:
    """Narrow inc.gold_output_ids in place per posture, using the SAME predicate as
    filter_include_for_posture but evaluated against the underlying asset's true
    (effective_provenance) source/license -- a gold output's own source/license is a
    calibration-generator decoy value and must never gate itself."""
    from .licensing import normalize_license

    keep: set[int] = set()
    for oid in inc.gold_output_ids:
        if oid in gated:
            continue
        o = db.get(ModelOutput, oid)
        if o is None:
            continue
        eff_source, eff_license = effective_provenance(db, o)
        if eff_source in HARD_EXCLUDE_SOURCES:
            continue
        redistributable = is_own_output(eff_source) or (
            normalize_license(eff_license) in REDISTRIBUTABLE_LICENSES
        )
        if posture == "redistribute":
            if redistributable and not is_commercial_model(eff_source):
                keep.add(oid)
        elif posture == "display":
            if redistributable or is_commercial_model(eff_source):
                keep.add(oid)
        else:
            raise ValueError(f"unknown posture {posture!r}")
    dropped = inc.gold_output_ids - keep
    if dropped:
        logger.info(
            "posture %s dropped %d gold output ids: %s", posture, len(dropped), sorted(dropped)
        )
    inc.gold_output_ids = keep


def check_licenses(db: Session, output_ids: set[int]) -> None:
    from .licensing import normalize_license

    for oid in sorted(output_ids):
        o = db.get(ModelOutput, oid)
        if o is None:
            continue
        if is_own_output(o.source):  # our own asset — exempt, nothing licenses us to ourselves
            continue
        if normalize_license(o.license) not in REDISTRIBUTABLE_LICENSES:
            raise LicenseError(oid, o.license)
