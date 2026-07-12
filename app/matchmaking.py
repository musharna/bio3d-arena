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


def _gen_key(o: ModelOutput):
    """Identity of the output's generator (the MODEL being ranked).

    A generator commonly owns several outputs on the same task, so "distinct outputs" is NOT
    "distinct models" — the arena must never serve a model against itself. Persisted outputs
    key on generator_id; not-yet-flushed in-memory outputs (unit fixtures) fall back to the
    Generator object's identity, which is stable within one session's identity map.
    """
    return o.generator_id if o.generator_id is not None else id(o.generator)


def _n_generators(outs: list[ModelOutput]) -> int:
    return len({_gen_key(o) for o in outs})


def _fresh_pair_exists(outs: list[ModelOutput], voted_pairs: set[frozenset[int]]) -> bool:
    """True if some same-paradigm, DIFFERENT-GENERATOR pair among `outs` is NOT in
    `voted_pairs` (the session's already-voted set). With an empty voted_pairs this reduces to
    'has any group with >=2 distinct generators'.

    Must mirror pick_pair's admissibility exactly — a task counted as pairable here but
    rejected by pick_pair yields a spurious None → intermittent 404 on /api/next."""
    for g in _paradigm_groups(outs).values():
        if _n_generators(g) < 2:
            continue
        if not voted_pairs:
            return True
        for i in range(len(g)):
            for j in range(i + 1, len(g)):
                if _gen_key(g[i]) == _gen_key(g[j]):
                    continue  # a model is never its own opponent
                if frozenset((g[i].id, g[j].id)) not in voted_pairs:
                    return True
    return False


def pick_task(
    db: Session,
    category_id: int | None = None,
    category_ids: set[int] | None = None,
    exclude_fn=None,
    voted_pairs=None,
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
    the /api/vote 409 'already voted on this pairing'.

    `category_ids` (a set) scopes to the active kingdom — when passed, it takes precedence
    over `category_id` (a single explicit `?category=` selector still uses `category_id`; the
    kingdom bucket uses `category_ids`). An EMPTY set means the kingdom has zero mapped
    categories, so no task is eligible (returns None), not 'all tasks'."""
    voted = voted_pairs or set()
    stmt = select(Task).where(Task.active.is_(True))
    if category_ids is not None:
        stmt = stmt.where(Task.category_id.in_(category_ids))
    elif category_id is not None:
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

    The two outputs must come from DIFFERENT generators: a generator often owns several
    outputs on one task, and pairing two of them ("TRELLIS vs TRELLIS") is a meaningless
    choice for the voter AND a (G, G) self-match that pollutes the Bradley-Terry fit. A
    paradigm group therefore qualifies only with >=2 distinct generators; a group whose
    outputs all come from one model is skipped (and a task with no such group returns None).

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
    remaining = [g for g in groups.values() if _n_generators(g) >= 2]

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
                if _gen_key(ordered[i]) == _gen_key(ordered[j]):
                    continue  # never pair a model against itself
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
    """Pick 4 same-paradigm (non-gold) outputs from 4 DISTINCT generators, biased toward
    least-compared.

    Mirrors pick_pair: exclude_fn filters the pool first (admissibility etc.), outputs are grouped
    by paradigm, the least-sampled qualifying group is chosen, and its 4 least-sampled outputs —
    one per generator — are returned in shuffled order.

    A group qualifies only with >=4 DISTINCT GENERATORS (not merely >=4 outputs): the K-wise
    ballot is rank-broken into pairwise comparisons, so the same model appearing twice on one
    ballot would manufacture a (G, G) self-match in the Bradley-Terry fit — and asks the voter
    to choose between two of one model's own outputs.

    `seen_quads` (set of frozenset of 4 output ids) is the session's already-served quads; a quad
    already seen is skipped. Returns None when no group yields a fresh 4-distinct-generator quad
    (caller falls back to pairwise)."""
    outputs = _real_outputs(task)
    if exclude_fn is not None:
        outputs = [o for o in outputs if not exclude_fn(o)]
    groups = _paradigm_groups(outputs)
    seen = seen_quads or set()
    remaining = [g for g in groups.values() if _n_generators(g) >= 4]
    while remaining:
        best = min(min(o.n_comparisons for o in g) for g in remaining)
        tied = [g for g in remaining if min(o.n_comparisons for o in g) == best]
        chosen = random.choice(tied)
        ordered = list(chosen)
        random.shuffle(ordered)
        ordered.sort(key=lambda o: o.n_comparisons)
        # Least-sampled-first, at most one output per generator (the group is known to hold
        # >=4 distinct generators, so this always fills to 4).
        quad: list[ModelOutput] = []
        used: set = set()
        for o in ordered:
            k = _gen_key(o)
            if k in used:
                continue
            used.add(k)
            quad.append(o)
            if len(quad) == 4:
                break
        if frozenset(o.id for o in quad) not in seen:
            random.shuffle(quad)
            return quad
        remaining = [g for g in remaining if g is not chosen]
    return None


def total_votes(db: Session) -> int:
    from .models import Vote

    return db.execute(select(func.count(Vote.id))).scalar_one()
