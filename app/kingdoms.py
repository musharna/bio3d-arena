"""Kingdom ⇄ category mapping. Kingdom is a display/scope grouping OVER categories;
there is no `kingdom` column (see docs plan). Buckets are closed and small, so a static
map (mirroring app/paradigms.py) is the source of truth — unit-testable, no migration."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Category

KINGDOMS = ("plants", "fungi", "animals")

CATEGORY_SLUGS_IN: dict[str, frozenset[str]] = {
    "plants": frozenset({"plants", "synthetic-plants"}),
    "fungi": frozenset({"fungi"}),
    "animals": frozenset({"animals"}),
}
KINGDOM_OF: dict[str, str] = {
    slug: kingdom for kingdom, slugs in CATEGORY_SLUGS_IN.items() for slug in slugs
}
KINGDOM_EMOJI = {"all": "🧬", "plants": "🌿", "fungi": "🍄", "animals": "🐾"}
KINGDOM_LABEL = {"all": "All kingdoms", "plants": "Plants", "fungi": "Fungi", "animals": "Animals"}
# Latin (taxonomic-kingdom) name shown as the italic subtitle on the home kingdom cards.
KINGDOM_LATIN = {"plants": "Plantae", "fungi": "Fungi", "animals": "Animalia"}


def normalize_kingdom(value: str | None) -> str:
    if not value:
        return "all"
    v = value.strip().lower()
    return v if v in KINGDOMS else "all"


def category_ids_for_kingdom(db: Session, kingdom: str) -> set[int] | None:
    kingdom = normalize_kingdom(kingdom)
    if kingdom == "all":
        return None
    slugs = CATEGORY_SLUGS_IN[kingdom]
    return set(db.execute(select(Category.id).where(Category.slug.in_(slugs))).scalars())
