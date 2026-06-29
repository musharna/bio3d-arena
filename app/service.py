"""Vote application + leaderboard recomputation — the glue between votes and ranks.

On each vote we apply an online Elo update for instant feedback. The authoritative
leaderboard is recomputed in batch with Bradley-Terry + bootstrap CIs over the
full decisive-vote record.
"""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import config, ranking
from .sourcing import is_reference_scan, is_untextured_output
from .models import (
    Category,
    Comparison,
    Criterion,
    Generator,
    JudgeRating,
    JudgeVote,
    Metric,
    ModelOutput,
    Rating,
    Task,
    TaskDifficulty,
    Vote,
    VoterSession,
)


def get_or_create_rating(
    db: Session, generator_id: int, criterion_id: int, category_id: int | None = None
) -> Rating:
    stmt = select(Rating).where(
        Rating.generator_id == generator_id,
        Rating.criterion_id == criterion_id,
        Rating.category_id.is_(None) if category_id is None else Rating.category_id == category_id,
    )
    rating = db.execute(stmt).scalars().first()
    if rating is None:
        rating = Rating(
            generator_id=generator_id, criterion_id=criterion_id, category_id=category_id
        )
        db.add(rating)
        db.flush()
    return rating


def apply_vote(db: Session, vote: Vote) -> None:
    """Record bookkeeping for a vote: bump comparison counts + online Elo.

    Elo is updated on the global (category-agnostic) scope for the comparison's
    criterion. 'bad' votes are recorded but do not move Elo.
    """
    comparison = db.get(Comparison, vote.comparison_id)
    out_a = db.get(ModelOutput, comparison.output_a_id)
    out_b = db.get(ModelOutput, comparison.output_b_id)
    out_a.n_comparisons += 1
    out_b.n_comparisons += 1

    if vote.winner == "bad":
        return

    score_a = {"a": 1.0, "b": 0.0, "tie": 0.5}[vote.winner]
    ra = get_or_create_rating(db, out_a.generator_id, comparison.criterion_id)
    rb = get_or_create_rating(db, out_b.generator_id, comparison.criterion_id)
    new_a, new_b = ranking.elo_update(ra.elo, rb.elo, score_a, k=config.ELO_K)
    ra.elo, rb.elo = new_a, new_b
    ra.n_games += 1
    rb.n_games += 1


def reference_scan_generator_ids(db: Session) -> set[int]:
    """Generator ids whose outputs are raw-scan/volumetric GT references.

    These are excluded from the Mode-A perceptual ranking (leaderboard / significance /
    BT) — they are ground-truth anchors, not generative methods, so ranking them
    perceptually makes "a raw scan beats every generator" the marquee result. They remain
    in the Mode-B benchmark board and the GT reference panel.
    """
    rows = db.execute(select(ModelOutput.generator_id, ModelOutput.source)).all()
    return {gid for gid, src in rows if is_reference_scan(src)}


def untextured_generator_ids(db: Session) -> set[int]:
    """Generators ALL of whose votable outputs are flagged geometry-only (flat grey blobs).

    Excluded from the Mode-A perceptual ranking + vote pool: an untextured render loses votes
    for reasons unrelated to shape quality (texture confound). Per-generator (only when every
    votable output is flagged) so a generator with any real textured output is never dropped.
    """
    outs = db.execute(select(ModelOutput).where(ModelOutput.is_gold.is_(False))).scalars().all()
    flagged: dict[int, tuple[int, int]] = {}
    for o in outs:
        u, t = flagged.get(o.generator_id, (0, 0))
        flagged[o.generator_id] = (u + int(is_untextured_output(o)), t + 1)
    return {gid for gid, (u, t) in flagged.items() if t > 0 and u == t}


def mode_a_excluded_generator_ids(db: Session) -> set[int]:
    """Generators excluded from the Mode-A perceptual ranking: GT reference scans (not generative
    methods) ∪ fully-untextured generators (flat-grey-blob renders confound perceptual votes)."""
    return reference_scan_generator_ids(db) | untextured_generator_ids(db)


def generator_display_names(db: Session) -> dict[int, str]:
    """Map generator_id → a UNIQUE display label.

    Several generators share a `name` (e.g. 8 distinct XfrogPlants variants all named
    "XfrogPlants (botanical)"), which makes board rows indistinguishable. When a name is
    shared, append a disambiguator derived from the (unique) slug; otherwise use the name.
    """
    gens = db.execute(select(Generator)).scalars().all()
    by_name: dict[str, list[Generator]] = {}
    for g in gens:
        by_name.setdefault(g.name, []).append(g)
    out: dict[int, str] = {}
    for name, group in by_name.items():
        if len(group) == 1:
            out[group[0].id] = name
            continue
        for g in group:
            # slug like "xfrog-AG15-s2" → suffix "AG15-s2"; "sketchfab-rose-rugosa" → "rose-rugosa".
            suffix = g.slug.split("-", 1)[-1] if "-" in g.slug else g.slug
            out[g.id] = f"{name} · {suffix}"
    return out


def _matches_for_scope(
    db: Session, criterion_id: int, category_id: int | None, include_ties: bool = True
) -> list[tuple[int, int]]:
    """Decisive (winner_gen, loser_gen) pairs for a (criterion, category) scope.

    A 'tie' is credited as a split — one win in each direction — so ties inform
    Bradley-Terry without a separate tie parameter. 'bad' votes are excluded.
    category_id=None means the global scope (all categories).
    """
    # Exclude gold attention-check comparisons, and (left outer join) any vote from
    # a session whose trust has fallen below TRUST_THRESHOLD — anti-abuse gating.
    stmt = (
        select(Vote, Comparison)
        .join(Comparison, Vote.comparison_id == Comparison.id)
        .outerjoin(VoterSession, VoterSession.session_id == Vote.session_id)
        .where(
            Comparison.criterion_id == criterion_id,
            Comparison.is_gold.is_(False),
            (VoterSession.trust.is_(None)) | (VoterSession.trust >= config.TRUST_THRESHOLD),
        )
    )
    if category_id is not None:
        stmt = stmt.join(Task, Comparison.task_id == Task.id).where(Task.category_id == category_id)

    ref_gens = mode_a_excluded_generator_ids(db)
    matches: list[tuple[int, int]] = []
    for vote, comparison in db.execute(stmt).all():
        if vote.winner == "bad":
            continue
        gen_a = db.get(ModelOutput, comparison.output_a_id).generator_id
        gen_b = db.get(ModelOutput, comparison.output_b_id).generator_id
        if gen_a in ref_gens or gen_b in ref_gens:
            continue  # GT/reference scans are not perceptual competitors (Mode-A exclusion)
        if vote.winner == "a":
            matches.append((gen_a, gen_b))
        elif vote.winner == "b":
            matches.append((gen_b, gen_a))
        elif vote.winner == "tie" and include_ties:
            matches.append((gen_a, gen_b))
            matches.append((gen_b, gen_a))
    return matches


def _players_for_scope(db: Session, category_id: int | None) -> list[int]:
    """Generators eligible for a scope's leaderboard (gold/decoy outputs excluded)."""
    stmt = select(ModelOutput.generator_id).where(ModelOutput.is_gold.is_(False))
    if category_id is not None:
        stmt = stmt.join(Task, ModelOutput.task_id == Task.id).where(
            Task.category_id == category_id
        )
    ref_gens = mode_a_excluded_generator_ids(db)
    return sorted({gid for gid in db.execute(stmt).scalars().all() if gid not in ref_gens})


def recompute_scope(
    db: Session, criterion: Criterion, category_id: int | None, commit: bool = True
) -> dict:
    """Refit Bradley-Terry for one (criterion, category) scope and cache Rating rows."""
    matches = _matches_for_scope(db, criterion.id, category_id)
    players = sorted(set(_players_for_scope(db, category_id)) | {p for m in matches for p in m})
    result = ranking.bradley_terry(players, matches, bootstrap=config.BT_BOOTSTRAP)
    for gid in players:
        rating = get_or_create_rating(db, gid, criterion.id, category_id)
        rating.bt_score = result.scores.get(gid, ranking.BT_BASE)
        rating.bt_lower = result.lower.get(gid, ranking.BT_BASE)
        rating.bt_upper = result.upper.get(gid, ranking.BT_BASE)
        rating.n_games = int(result.n_games.get(gid, 0))
    if commit:
        db.commit()
    return {"matches": len(matches), "players": len(players)}


def recompute_leaderboard(db: Session, criterion_slug: str = "overall") -> dict:
    """Backward-compatible single-criterion GLOBAL recompute."""
    criterion = (
        db.execute(select(Criterion).where(Criterion.slug == criterion_slug)).scalars().first()
    )
    if criterion is None:
        return {"status": "no-such-criterion"}
    detail = recompute_scope(db, criterion, category_id=None)
    return {"status": "ok", **detail}


def recompute_all(db: Session) -> dict:
    """Recompute every (criterion × {global + each category}) leaderboard scope."""
    criteria = db.execute(select(Criterion)).scalars().all()
    categories = db.execute(select(Category)).scalars().all()
    n_scopes = 0
    for criterion in criteria:
        recompute_scope(db, criterion, category_id=None, commit=False)
        n_scopes += 1
        for cat in categories:
            recompute_scope(db, criterion, category_id=cat.id, commit=False)
            n_scopes += 1
    db.commit()
    return {
        "status": "ok",
        "scopes": n_scopes,
        "criteria": len(criteria),
        "categories": len(categories),
    }


def _judge_matches_for_scope(
    db: Session, criterion_id: int, view_condition: str, include_ties: bool = True
) -> list[tuple[int, int]]:
    """Decisive (winner_gen, loser_gen) pairs from JudgeVote for one (criterion, condition).
    Tie → split both directions; bad excluded. Mirrors _matches_for_scope (human)."""
    stmt = select(JudgeVote).where(
        JudgeVote.criterion_id == criterion_id,
        JudgeVote.view_condition == view_condition,
    )
    ref_gens = mode_a_excluded_generator_ids(db)
    matches: list[tuple[int, int]] = []
    for jv in db.execute(stmt).scalars():
        if jv.winner == "bad":
            continue
        gen_a = db.get(ModelOutput, jv.output_a_id).generator_id
        gen_b = db.get(ModelOutput, jv.output_b_id).generator_id
        if gen_a in ref_gens or gen_b in ref_gens:
            continue  # GT/reference scans are not perceptual competitors (Mode-A exclusion)
        if jv.winner == "a":
            matches.append((gen_a, gen_b))
        elif jv.winner == "b":
            matches.append((gen_b, gen_a))
        elif jv.winner == "tie" and include_ties:
            matches.append((gen_a, gen_b))
            matches.append((gen_b, gen_a))
    return matches


def _get_or_create_judge_rating(
    db: Session, generator_id: int, criterion_id: int, view_condition: str
) -> JudgeRating:
    stmt = select(JudgeRating).where(
        JudgeRating.generator_id == generator_id,
        JudgeRating.criterion_id == criterion_id,
        JudgeRating.view_condition == view_condition,
        JudgeRating.category_id.is_(None),
    )
    r = db.execute(stmt).scalars().first()
    if r is None:
        r = JudgeRating(
            generator_id=generator_id,
            criterion_id=criterion_id,
            view_condition=view_condition,
            category_id=None,
        )
        db.add(r)
        db.flush()
    return r


def _judge_model_for_scope(db: Session, criterion_id: int, view_condition: str) -> str:
    """The judge model that produced this scope's votes (modal value if mixed).

    Falls back to the configured default only when the scope has no votes yet, so the
    cached JudgeRating reflects what actually ran rather than a hardcoded constant."""
    counts: dict[str, int] = {}
    for model in db.execute(
        select(JudgeVote.judge_model).where(
            JudgeVote.criterion_id == criterion_id,
            JudgeVote.view_condition == view_condition,
        )
    ).scalars():
        counts[model] = counts.get(model, 0) + 1
    if not counts:
        from . import judge

        return judge.JUDGE_MODEL
    return max(counts, key=lambda m: counts[m])


def recompute_judge_scope(
    db: Session, criterion: Criterion, view_condition: str, commit: bool = True
) -> dict:
    """Refit Bradley-Terry over JudgeVote for (criterion, condition); cache JudgeRating."""
    matches = _judge_matches_for_scope(db, criterion.id, view_condition)
    judge_model = _judge_model_for_scope(db, criterion.id, view_condition)
    players = sorted(set(_players_for_scope(db, None)) | {p for m in matches for p in m})
    result = ranking.bradley_terry(players, matches, bootstrap=config.BT_BOOTSTRAP)
    for gid in players:
        r = _get_or_create_judge_rating(db, gid, criterion.id, view_condition)
        r.bt_score = result.scores.get(gid, ranking.BT_BASE)
        r.bt_lower = result.lower.get(gid, ranking.BT_BASE)
        r.bt_upper = result.upper.get(gid, ranking.BT_BASE)
        r.n_games = int(result.n_games.get(gid, 0))
        r.judge_model = judge_model
    if commit:
        db.commit()
    return {"matches": len(matches), "players": len(players)}


def recompute_judge_all(db: Session, view_condition: str = "multi4") -> dict:
    """Recompute the VLM leaderboard for every criterion under one view condition."""
    criteria = db.execute(select(Criterion)).scalars().all()
    for criterion in criteria:
        recompute_judge_scope(db, criterion, view_condition, commit=False)
    db.commit()
    return {"status": "ok", "view_condition": view_condition, "criteria": len(criteria)}


def tier_perceptual_ranking(
    db: Session, criterion_slug: str = "overall", view_condition: str = "multi4"
) -> list[dict]:
    """Per-difficulty-tier VLM-judge Bradley-Terry ranking — does the perceptual winner shift
    by difficulty? Read-only (no Rating writes). Uses JUDGE votes because human votes are too
    sparse on the hard tier to rank. Returns one block per tier (canonical order) with ranked
    rows; a tier with too few votes comes back with an empty `rows` and its `n_matches` count.
    """
    from collections import defaultdict

    from .difficulty import TIERS
    from .models import Criterion, JudgeVote, TaskDifficulty

    empty = [{"tier": t, "rows": [], "n_matches": 0} for t in TIERS]
    crit = db.execute(select(Criterion).where(Criterion.slug == criterion_slug)).scalars().first()
    if crit is None:
        return empty

    tier_by_task = {td.task_id: td.tier for td in db.execute(select(TaskDifficulty)).scalars()}
    excluded = mode_a_excluded_generator_ids(db)
    names = generator_display_names(db)

    matches_by_tier: dict[str, list[tuple[int, int]]] = defaultdict(list)
    jvs = db.execute(
        select(JudgeVote).where(
            JudgeVote.criterion_id == crit.id, JudgeVote.view_condition == view_condition
        )
    ).scalars()
    for jv in jvs:
        if jv.winner == "bad":
            continue
        tier = tier_by_task.get(jv.task_id)
        if tier is None:
            continue
        gen_a = db.get(ModelOutput, jv.output_a_id).generator_id
        gen_b = db.get(ModelOutput, jv.output_b_id).generator_id
        if gen_a in excluded or gen_b in excluded:
            continue
        if jv.winner == "a":
            matches_by_tier[tier].append((gen_a, gen_b))
        elif jv.winner == "b":
            matches_by_tier[tier].append((gen_b, gen_a))
        elif jv.winner == "tie":
            matches_by_tier[tier].append((gen_a, gen_b))
            matches_by_tier[tier].append((gen_b, gen_a))

    out = []
    for tier in TIERS:
        matches = matches_by_tier.get(tier, [])
        players = sorted({p for m in matches for p in m})
        rows = []
        if players:
            result = ranking.bradley_terry(players, matches, bootstrap=config.BT_BOOTSTRAP)
            rows = sorted(
                (
                    {
                        "generator": names.get(p, str(p)),
                        "bt_score": round(result.scores.get(p, ranking.BT_BASE), 1),
                        "n_games": int(result.n_games.get(p, 0)),
                    }
                    for p in players
                ),
                key=lambda r: r["bt_score"],
                reverse=True,
            )
        out.append({"tier": tier, "rows": rows, "n_matches": len(matches)})
    return out


def compute_significance(
    db: Session, criterion_slug: str = "overall", category_id: int | None = None
) -> dict:
    """Pairwise P(A ranks above B) for a scope — "is A *meaningfully* ahead of B?"."""
    criterion = (
        db.execute(select(Criterion).where(Criterion.slug == criterion_slug)).scalars().first()
    )
    if criterion is None:
        return {"status": "no-such-criterion"}
    matches = _matches_for_scope(db, criterion.id, category_id)
    players = sorted(set(_players_for_scope(db, category_id)) | {p for m in matches for p in m})
    result = ranking.significance_matrix(players, matches, bootstrap=config.BT_BOOTSTRAP)
    names = generator_display_names(db)

    ranked = []
    for pid in result.order:
        pn = result.p_beats_next.get(pid)
        ranked.append(
            {
                "generator": names.get(pid, str(pid)),
                "score": round(result.scores.get(pid, ranking.BT_BASE), 1),
                "p_beats_next": (round(pn, 3) if pn is not None else None),
                # A generator is "significantly" ahead of the next when it beats it in
                # >=95% of bootstrap resamples.
                "sig_above_next": (pn is not None and pn >= 0.95),
            }
        )
    labels = [names.get(pid, str(pid)) for pid in result.order]
    matrix = [
        [(1.0 if i == j else round(result.p_better.get((i, j), 0.5), 3)) for j in result.order]
        for i in result.order
    ]
    return {
        "status": "ok",
        "criterion": criterion_slug,
        "n_matches": len(matches),
        "labels": labels,
        "ranked": ranked,
        "matrix": matrix,  # matrix[i][j] = P(labels[i] ranks above labels[j])
    }


def compute_bias(db: Session) -> dict:
    """Position/format-bias audit over all votes.

    left_win_rate ≈ 0.5 means no position bias (A/B sides are randomized). A value
    far from 0.5 signals voters favour a side regardless of content. cross_format
    counts comparisons that pit different asset formats against each other (a
    format-confound risk); format_win_rate breaks those down.
    """
    rows = db.execute(
        select(Vote, Comparison).join(Comparison, Vote.comparison_id == Comparison.id)
    ).all()
    a = b = tie = bad = cross_format = 0
    fmt_wins: dict[str, int] = defaultdict(int)
    fmt_games: dict[str, int] = defaultdict(int)
    for vote, comp in rows:
        out_a = db.get(ModelOutput, comp.output_a_id)
        out_b = db.get(ModelOutput, comp.output_b_id)
        is_cross = out_a.asset_format != out_b.asset_format
        if is_cross:
            cross_format += 1
        if vote.winner == "a":
            a += 1
        elif vote.winner == "b":
            b += 1
        elif vote.winner == "tie":
            tie += 1
        else:
            bad += 1
        if is_cross and vote.winner in ("a", "b"):
            win_out, lose_out = (out_a, out_b) if vote.winner == "a" else (out_b, out_a)
            fmt_wins[win_out.asset_format] += 1
            fmt_games[win_out.asset_format] += 1
            fmt_games[lose_out.asset_format] += 1
    n = len(rows)
    decisive = a + b

    # Gold attention-check + trust stats.
    sessions = db.execute(select(VoterSession)).scalars().all()
    gold_seen = sum(s.gold_seen for s in sessions)
    gold_passed = sum(s.gold_passed for s in sessions)
    low_trust = sum(1 for s in sessions if s.trust < config.TRUST_THRESHOLD)

    return {
        "n_votes": n,
        "decisive": decisive,
        "left_win_rate": round(a / decisive, 3) if decisive else None,
        "tie_rate": round(tie / n, 3) if n else None,
        "bad_rate": round(bad / n, 3) if n else None,
        "cross_format_comparisons": cross_format,
        "format_win_rate": {f: round(fmt_wins[f] / g, 3) for f, g in fmt_games.items() if g},
        "gold_checks_served": gold_seen,
        "gold_pass_rate": round(gold_passed / gold_seen, 3) if gold_seen else None,
        "sessions": len(sessions),
        "low_trust_sessions": low_trust,
    }


# Below this many total Mode-A votes a generator's rank is flagged "provisional" — the
# transparency knob that answers "is this rank trustworthy yet?" on the coverage page.
FIRM_VOTE_THRESHOLD = 30


def coverage_summary(db: Session) -> dict:
    """Per-generator + per-task coverage & vote-count disclosure (governance transparency).

    Read-only aggregate powering /coverage and /api/coverage.json. Surfaces, per generator,
    how many votes/outputs/tasks back its rank (+ a firm/provisional confidence flag), and per
    task, how thinly or richly it is covered — the data the post-2025 "leaderboard illusion"
    critique asks every arena to publish, and the substrate for a future phylogenetic map."""
    names = generator_display_names(db)
    excluded = mode_a_excluded_generator_ids(db)

    gen_rows = []
    for g in db.execute(select(Generator)).scalars().all():
        outs = [o for o in g.outputs if not o.is_gold]
        if not outs:
            continue  # gold-only / empty generators don't appear on the public board
        votes = sum(o.n_comparisons for o in outs)
        gen_rows.append(
            {
                "generator": names.get(g.id, g.name),
                "kind": g.kind,
                "tasks": len({o.task_id for o in outs}),
                "outputs": len(outs),
                "votes": votes,
                "excluded_from_mode_a": g.id in excluded,
                "confidence": "firm" if votes >= FIRM_VOTE_THRESHOLD else "provisional",
            }
        )
    gen_rows.sort(key=lambda r: (-r["votes"], r["generator"]))

    task_rows = []
    for t in db.execute(select(Task).where(Task.active.is_(True))).scalars().all():
        outs = [o for o in t.outputs if not o.is_gold]
        cat = db.get(Category, t.category_id)
        diff = (
            db.execute(select(TaskDifficulty).where(TaskDifficulty.task_id == t.id))
            .scalars()
            .first()
        )
        mode_a_votes = db.execute(
            select(func.count(Vote.id))
            .select_from(Vote)
            .join(Comparison, Vote.comparison_id == Comparison.id)
            .where(Comparison.task_id == t.id, Comparison.is_gold.is_(False))
        ).scalar_one()
        judge_votes = db.execute(
            select(func.count(JudgeVote.id)).where(JudgeVote.task_id == t.id)
        ).scalar_one()
        out_ids = [o.id for o in outs]
        has_mode_b = bool(
            out_ids
            and db.execute(
                select(func.count(Metric.id)).where(Metric.output_id.in_(out_ids))
            ).scalar_one()
        )
        task_rows.append(
            {
                "task": t.title,
                "category": cat.name if cat else "",
                "tier": diff.tier if diff else None,
                "generators": len({o.generator_id for o in outs}),
                "outputs": len(outs),
                "mode_a_votes": mode_a_votes,
                "judge_votes": judge_votes,
                "has_mode_b": has_mode_b,
            }
        )
    task_rows.sort(key=lambda r: (-r["outputs"], r["task"]))

    return {"generators": gen_rows, "tasks": task_rows}
