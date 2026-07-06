"""Curated promotion boundary for the public instance (SP1).

Resolves the exact row-id sets that may be published, and enforces the license gate.
Pure DB reads; no filesystem, no serialization (that's scripts/export_public.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Generator, GoldPair, ModelOutput, Task

REDISTRIBUTABLE_LICENSES = frozenset(
    {
        "CC0-1.0",
        "CC-BY-4.0",
        "CC-BY-SA-4.0",
        "CC-BY-3.0",
        "CC-BY-2.0",
        "PUBLIC-DOMAIN",
        "ODbL-1.0",
    }
)

# Generators never promoted to the public instance, regardless of the curator's --generators
# allowlist. Demeter + Helios are internal procedural_expert testers (agrigen) whose outputs are
# inconsistent on the public arena (some render untextured/colorless); kept internal-only. A
# deny-list here makes the exclusion durable — an operator can't accidentally publish them.
PUBLIC_EXCLUDED_GENERATORS = frozenset({"demeter", "helios"})


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
    inc = IncludeSet()
    allowed = [s for s in generator_slugs if s not in PUBLIC_EXCLUDED_GENERATORS]
    inc.generator_ids = {
        g.id
        for g in db.execute(select(Generator).where(Generator.slug.in_(allowed))).scalars()
        if g.slug not in PUBLIC_EXCLUDED_GENERATORS
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


def check_licenses(db: Session, output_ids: set[int]) -> None:
    for oid in sorted(output_ids):
        o = db.get(ModelOutput, oid)
        if o is None:
            continue
        if o.source == "bio3d-arena":  # our own asset — exempt
            continue
        if o.license not in REDISTRIBUTABLE_LICENSES:
            raise LicenseError(oid, o.license)
