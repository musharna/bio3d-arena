"""Per-organism pages — the corpus indexed by its subject rather than by its models.

The benchmark's distinctive claim is about ORGANISMS: how well generative 3D models reproduce a
maize plant, a lion's mane mushroom, a monarch butterfly. Until now the site exposed that claim
only through pages about the models, so the queries the corpus is uniquely able to answer —
"Zea mays 3D model", "Amanita muscaria reconstruction" — had nowhere to land.

Two structural decisions, both pinned by tests/test_organism_pages.py:

**One page per organism, not per task.** Several organisms carry two tasks (a reconstruction
and a botanical-plausibility variant — Arabidopsis is tasks 10 and 14, Zea mays 12 and 16). A
page per task would aim two near-identical pages at the same query and split the ranking
between them. Consolidating gives 16 pages across 20 active tasks, each stronger than either
half would have been.

**The key is the one the galleries already use.** `service._gallery_slug` turns a task title
into `zea_mays`, and the reference galleries are stored under exactly that name. The URL form
swaps the separator for a hyphen (conventional in a path segment) and nothing else, so the two
cannot drift into a second identifier — the failure mode this repo keeps rediscovering.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload, selectinload

from . import kingdoms, paradigms, service
from .models import Category, Comparison, ModelOutput, Task, TaskDifficulty, Vote
from .service import _gallery_slug


def binomial_of_title(title: str) -> str:
    """'Zea mays — single-image → 3D reconstruction' -> 'Zea mays'.

    The em-dash split is the convention already used by app/difficulty.py, app/service.py and
    the gallery-sourcing scripts. Deliberately delegating rather than re-deriving: a second
    parser here would be a fourth copy of the same rule.
    """
    return title.split("—")[0].strip()


def url_slug(binomial: str) -> str:
    """'Zea mays' -> 'zea-mays'. The gallery key with a path-friendly separator."""
    return _gallery_slug(binomial).replace("_", "-")


def gallery_slug(slug: str) -> str:
    """'zea-mays' -> 'zea_mays'. The inverse of the separator swap in `url_slug`."""
    return slug.replace("-", "_")


def _active_tasks(db: Session) -> list[Task]:
    """The live corpus, with everything both callers walk already loaded.

    Both `organism_index` and `build_organism` iterate `task.outputs` and, through it,
    `output.generator`. Left lazy, that is one round trip per task and another per output —
    ~1.2 and ~1.0 respectively when measured on the real corpus, which is what put /tasks at
    137 statements and /organisms/{slug} at 72. Same defect as the ranking scan in
    `service._scope_rows`, one module over; eager loading makes it a fixed handful.
    """
    stmt = (
        select(Task)
        .where(Task.active.is_(True))
        .order_by(Task.id)
        .options(
            joinedload(Task.category),
            selectinload(Task.outputs).joinedload(ModelOutput.generator),
        )
    )
    return list(db.execute(stmt).unique().scalars())


def organism_index(db: Session) -> list[dict]:
    """Every organism in the active corpus, with enough to render an index card.

    Ordered by kingdom then binomial so the index reads as a taxonomy rather than as whatever
    order the tasks were seeded in.
    """
    by_slug: dict[str, dict] = {}
    for task in _active_tasks(db):
        binomial = binomial_of_title(task.title)
        if not binomial:
            continue
        slug = url_slug(binomial)
        cat = task.category
        entry = by_slug.setdefault(
            slug,
            {
                "slug": slug,
                "binomial": binomial,
                "kingdom": kingdoms.KINGDOM_OF.get(cat.slug, "all") if cat else "all",
                "n_tasks": 0,
                "n_outputs": 0,
            },
        )
        entry["n_tasks"] += 1
        entry["n_outputs"] += sum(1 for o in task.outputs if not o.is_gold and o.hidden_at is None)
    order = {"plants": 0, "fungi": 1, "animals": 2, "all": 3}
    return sorted(
        by_slug.values(), key=lambda e: (order.get(e["kingdom"], 9), e["binomial"].lower())
    )


def build_organism(db: Session, slug: str) -> dict | None:
    """Everything an organism page renders, or None when no active task covers that organism.

    None rather than an empty page on purpose: an organism with no task has nothing to say, and
    a thin auto-generated page is worse than a 404 both for a reader and for a crawler.
    """
    tasks = [t for t in _active_tasks(db) if url_slug(binomial_of_title(t.title)) == slug]
    if not tasks:
        return None

    binomial = binomial_of_title(tasks[0].title)
    task_ids = [t.id for t in tasks]
    cat: Category | None = tasks[0].category
    kingdom = kingdoms.KINGDOM_OF.get(cat.slug, "all") if cat else "all"

    # Human votes per task, counted the way /tasks counts them (non-gold decisive votes) so the
    # two pages can never report different totals for the same task.
    vote_counts: dict[int, int] = dict(
        db.execute(
            select(Comparison.task_id, func.count(Vote.id))
            .select_from(Vote)
            .join(Comparison, Vote.comparison_id == Comparison.id)
            .where(Comparison.is_gold.is_(False), Comparison.task_id.in_(task_ids))
            .group_by(Comparison.task_id)
        ).all()
    )
    tier_by_task: dict[int, str] = dict(
        db.execute(
            select(TaskDifficulty.task_id, TaskDifficulty.tier).where(
                TaskDifficulty.task_id.in_(task_ids)
            )
        ).all()
    )

    task_rows = [
        {
            "id": t.id,
            "title": t.title,
            "prompt": t.prompt,
            "tier": tier_by_task.get(t.id),
            "votes": vote_counts.get(t.id, 0),
            "n_outputs": sum(1 for o in t.outputs if not o.is_gold and o.hidden_at is None),
        }
        for t in tasks
    ]

    # Which models attempted this organism. Same visibility rule as /models and the sitemap,
    # read from its single source: app-hidden testers are absent everywhere, not just here.
    hidden_ids = service.app_hidden_generator_ids(db)
    names = service.generator_display_names(db)
    models: dict[int, dict] = {}
    for t in tasks:
        for o in t.outputs:
            if o.is_gold or o.hidden_at is not None:
                continue
            gen = o.generator
            if gen is None or gen.id in hidden_ids:
                continue
            row = models.setdefault(
                gen.id,
                {
                    "slug": gen.slug,
                    "name": names.get(gen.id, gen.name),
                    "paradigm": gen.paradigm,
                    "paradigm_display": paradigms.DISPLAY_NAMES.get(gen.paradigm, gen.paradigm)
                    if gen.paradigm
                    else "",
                    "outputs": 0,
                    "votes": 0,
                },
            )
            row["outputs"] += 1
            row["votes"] += o.n_comparisons
    order = {p: i for i, p in enumerate(paradigms.PARADIGMS)}
    model_rows = sorted(
        models.values(),
        key=lambda m: (order.get(m["paradigm"], len(order)), -m["votes"], m["name"].lower()),
    )

    # Reference photographs: the QA-passed CC gallery for this organism, carrying the
    # attribution its licences require. Reused from the arena rather than re-read, so a photo
    # pulled from the vote UI cannot linger here.
    references: list[dict] = []
    seen: set[str] = set()
    for t in tasks:
        for ref in service.reference_images_for_task(db, t):
            url = ref.get("url")
            if url and url not in seen:
                seen.add(url)
                references.append(ref)

    return {
        "slug": slug,
        "binomial": binomial,
        "kingdom": kingdom,
        "kingdom_label": kingdoms.KINGDOM_LABEL.get(kingdom, kingdom),
        "kingdom_emoji": kingdoms.KINGDOM_EMOJI.get(kingdom, kingdoms.KINGDOM_EMOJI["all"]),
        "category": cat.name if cat else "",
        "tier": next((r["tier"] for r in task_rows if r["tier"]), None),
        "tasks": task_rows,
        "models": model_rows,
        "references": references,
        "n_outputs": sum(r["n_outputs"] for r in task_rows),
        "votes": sum(r["votes"] for r in task_rows),
    }


def breadcrumbs(org: dict, base_url: str) -> dict:
    """schema.org BreadcrumbList for an organism page: Home › Organisms › <binomial>.

    Breadcrumbs are the structured-data claim this page can actually support. The tempting
    alternative is schema.org/Dataset, which is what Google Dataset Search reads — but a Dataset
    is expected to describe something downloadable, and an organism page offers no distribution.
    Declaring one anyway to win a richer search result would be asserting something untrue about
    the page, which is the same standard the leaderboard holds itself to when it refuses to rank
    a model on four votes.
    """
    trail = [
        ("Home", "/"),
        ("Organisms", "/organisms"),
        (org["binomial"], f"/organisms/{org['slug']}"),
    ]
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": i, "name": name, "item": f"{base_url}{path}"}
            for i, (name, path) in enumerate(trail, start=1)
        ],
    }


def meta_description(org: dict) -> str:
    """A factual one-liner for the page's <meta name="description">.

    Built from counts that are true at render time and never from a rank: most entrants on this
    site are explicitly unranked, and a description asserting a standing the leaderboard refuses
    to assert would be the one dishonest sentence on an otherwise careful page.
    """

    def plural(n: int, word: str) -> str:
        return f"{n} {word}" if n == 1 else f"{n} {word}s"

    clauses = [
        f"{org['binomial']}: {plural(org['n_outputs'], 'AI-generated 3D model')} "
        f"from {plural(len(org['models']), 'generator')}"
    ]
    methods = len({m["paradigm"] for m in org["models"] if m["paradigm"]})
    if methods:
        clauses.append(f"across {plural(methods, 'generation method')}")
    clauses.append(
        "compared blind against reference photographs of the real organism on Taxon3D"
    )
    return ", ".join(clauses) + "."
