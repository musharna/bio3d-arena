"""Pair + task selection for the arena.

Prefers under-sampled outputs (fewest prior comparisons) so each vote buys the
most ranking information, with random tie-breaking and randomized A/B display
order to avoid position bias.
"""

from __future__ import annotations

import random
from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import GoldPair, ModelOutput, Task


def _real_outputs(task: Task) -> list[ModelOutput]:
    """Task outputs eligible for normal matchmaking (gold/decoy assets excluded)."""
    return [o for o in task.outputs if not o.is_gold]


def _paradigm_groups(outputs: list[ModelOutput]) -> dict[str, list[ModelOutput]]:
    """Group outputs by their generator's paradigm value."""
    groups: dict[str, list[ModelOutput]] = defaultdict(list)
    for o in outputs:
        groups[o.generator.paradigm].append(o)
    return groups


def _fresh_pair_exists(outs: list[ModelOutput], voted_pairs: set[frozenset[int]]) -> bool:
    """True if some same-paradigm pair among `outs` is NOT in `voted_pairs` (the session's
    already-voted set). With an empty voted_pairs this reduces to 'has any pairable group'."""
    for g in _paradigm_groups(outs).values():
        if len(g) < 2:
            continue
        if not voted_pairs:
            return True
        ids = [o.id for o in g]
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                if frozenset((ids[i], ids[j])) not in voted_pairs:
                    return True
    return False


def pick_task(
    db: Session, category_id: int | None = None, exclude_fn=None, voted_pairs=None
) -> Task | None:
    """Pick a random active task that has at least one votable (non-gold) pair the session
    has NOT already voted on.

    `exclude_fn(output) -> bool` must be the SAME filter pick_pair will apply, so a task
    is only a candidate if it still has >=2 outputs AFTER exclusion. Counting pre-exclusion
    here (while pick_pair filters post-exclusion) let pick_task return a task pick_pair then
    rejected → a spurious None → intermittent 404 on /api/next.

    `voted_pairs` (set of frozenset({a_id, b_id})) is the session's already-voted pairings;
    a task is only a candidate if it still has a FRESH pair after that exclusion too — else
    pick_task offers a task whose every pair pick_pair must skip, dead-ending the session on
    the /api/vote 409 'already voted on this pairing'."""
    voted = voted_pairs or set()
    stmt = select(Task).where(Task.active.is_(True))
    if category_id is not None:
        stmt = stmt.where(Task.category_id == category_id)
    tasks = db.execute(stmt).scalars().all()

    def is_candidate(t: Task) -> bool:
        outs = _real_outputs(t)
        if exclude_fn is not None:
            outs = [o for o in outs if not exclude_fn(o)]
        return _fresh_pair_exists(outs, voted)

    candidates = [t for t in tasks if is_candidate(t)]
    if not candidates:
        return None
    return random.choice(candidates)


def pick_gold_pair(db: Session) -> GoldPair | None:
    """Pick a random gold attention-check pair, if any are configured."""
    golds = db.execute(select(GoldPair)).scalars().all()
    return random.choice(golds) if golds else None


def pick_pair(
    db: Session, task: Task, exclude_fn=None, voted_pairs=None
) -> tuple[ModelOutput, ModelOutput] | None:
    """Pick two distinct (non-gold) outputs for the task, biased toward least-compared.

    `exclude_fn(output) -> bool` may optionally be passed to filter out specific
    outputs (e.g. reference-scan sources) before pair selection.

    `voted_pairs` (set of frozenset({a_id, b_id})) is the session's already-voted pairings;
    any pair in it is skipped so the served comparison can't 409 the /api/vote guard. If a
    least-sampled group's pairs are all voted, fall through to the next group; return None
    only when NO fresh pair remains (a clean end state → 404, not a dead-end re-serve).
    """
    outputs = _real_outputs(task)
    if exclude_fn is not None:
        outputs = [o for o in outputs if not exclude_fn(o)]
    groups = _paradigm_groups(outputs)
    voted = voted_pairs or set()
    remaining = [g for g in groups.values() if len(g) >= 2]

    # Consider paradigm groups least-sampled-first (preserving fairness), and within the
    # chosen group take the least-sampled pair NOT already voted. Among groups tied at the
    # global minimum, choose RANDOMLY — a plain min() breaks ties by dict insertion order,
    # which permanently starves every paradigm but the first-inserted one whenever they tie.
    while remaining:
        best = min(min(o.n_comparisons for o in g) for g in remaining)
        tied = [g for g in remaining if min(o.n_comparisons for o in g) == best]
        chosen = random.choice(tied)
        ordered = list(chosen)
        random.shuffle(ordered)
        ordered.sort(key=lambda o: o.n_comparisons)
        picked = None
        for i in range(len(ordered)):
            for j in range(i + 1, len(ordered)):
                if frozenset((ordered[i].id, ordered[j].id)) not in voted:
                    picked = (ordered[i], ordered[j])
                    break
            if picked:
                break
        if picked is not None:
            a, b = picked
            if random.random() < 0.5:
                a, b = b, a
            return a, b
        # every pair in this group is already voted → drop it and try another group
        remaining = [g for g in remaining if g is not chosen]
    return None


def pick_quad(
    db: Session, task: Task, exclude_fn=None, seen_quads=None
) -> list[ModelOutput] | None:
    """Pick 4 distinct same-paradigm (non-gold) outputs for the task, biased toward least-compared.

    Mirrors pick_pair: exclude_fn filters the pool first (admissibility etc.), outputs are grouped
    by paradigm, the least-sampled group with >=4 members is chosen, and its 4 least-sampled
    outputs are returned in shuffled order. `seen_quads` (set of frozenset of 4 output ids) is the
    session's already-served quads; a quad already seen is skipped. Returns None when no group has
    4 fresh outputs (caller falls back to pairwise)."""
    outputs = _real_outputs(task)
    if exclude_fn is not None:
        outputs = [o for o in outputs if not exclude_fn(o)]
    groups = _paradigm_groups(outputs)
    seen = seen_quads or set()
    remaining = [g for g in groups.values() if len(g) >= 4]
    while remaining:
        best = min(min(o.n_comparisons for o in g) for g in remaining)
        tied = [g for g in remaining if min(o.n_comparisons for o in g) == best]
        chosen = random.choice(tied)
        ordered = list(chosen)
        random.shuffle(ordered)
        ordered.sort(key=lambda o: o.n_comparisons)
        quad = ordered[:4]
        if frozenset(o.id for o in quad) not in seen:
            random.shuffle(quad)
            return quad
        remaining = [g for g in remaining if g is not chosen]
    return None


def total_votes(db: Session) -> int:
    from .models import Vote

    return db.execute(select(func.count(Vote.id))).scalar_one()
