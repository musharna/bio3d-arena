"""Vote application + leaderboard recomputation — the glue between votes and ranks.

On each vote we apply an online Elo update for instant feedback. The authoritative
leaderboard is recomputed in batch with Bradley-Terry + bootstrap CIs over the
full decisive-vote record.
"""

from __future__ import annotations

import datetime as dt
import functools
import json
from collections import defaultdict
from collections.abc import Callable, Iterable

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from . import config, kingdoms, matchmaking, paradigms, ranking
from .calibration import cohens_kappa
from .scope import is_assessable
from .paradigms import same_paradigm
from .sourcing import is_reference_scan, is_untextured_output
from .storage import get_storage
from .models import (
    Category,
    CommissionAttempt,
    Comparison,
    Criterion,
    Generator,
    JudgeRating,
    JudgeVote,
    KBallot,
    KingdomJudgeRating,
    KingdomRating,
    Metric,
    ModelOutput,
    ModelScope,
    Rating,
    Task,
    TaskDifficulty,
    TraitCalibration,
    TraitRubric,
    TraitScore,
    TraitVerdict,
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


def resolve_kballot(
    db: Session, ballot: KBallot, best_output_id: int | None, session_id: str
) -> int:
    """Resolve a K-ballot. best_output_id=None -> 'all bad' (0 relations). Otherwise expand into
    one (best beats loser) Comparison+Vote per loser, sharing ballot_id, each fed to apply_vote.
    Sets ballot.resolved. Returns the number of pairwise relations created. Caller commits."""
    import json as _json

    ballot.best_output_id = best_output_id
    ballot.resolved = True
    if best_output_id is None:
        return 0
    ids = _json.loads(ballot.output_ids_json)
    losers = [oid for oid in ids if oid != best_output_id]
    for loser in losers:
        comp = Comparison(
            task_id=ballot.task_id,
            output_a_id=best_output_id,
            output_b_id=loser,
            criterion_id=ballot.criterion_id,
            session_id=session_id,
            ballot_id=ballot.id,
        )
        db.add(comp)
        db.flush()
        vote = Vote(comparison_id=comp.id, winner="a", session_id=session_id)
        db.add(vote)
        db.flush()
        apply_vote(db, vote)
    return len(losers)


def _comparison_output_ids(pairs) -> set[int]:  # noqa: ANN001
    """Every output id referenced by a sequence of (vote, comparison) rows."""
    ids: set[int] = set()
    for _vote, comp in pairs:
        ids.add(comp.output_a_id)
        ids.add(comp.output_b_id)
    return ids


def _output_generator_ids(db: Session, pairs) -> dict[int, int]:  # noqa: ANN001
    """{output_id: generator_id} for every output the given comparisons reference, in ONE query.

    Replaces a `db.get(ModelOutput, ...)` per comparison side. That pattern was free on the
    internal instance — SQLite, in-process, a primary-key lookup is microseconds — and became the
    entire cost of the public leaderboard, where every lookup is a network round trip to managed
    Postgres. Measured on the live deploy: 1103 statements per `/leaderboard`, 965 of them these
    single-row selects, ~12s per render. Worse, it scaled with the vote count, which is the one
    number this project is trying to grow.
    """
    ids = _comparison_output_ids(pairs)
    if not ids:
        return {}
    rows = db.execute(
        select(ModelOutput.id, ModelOutput.generator_id).where(ModelOutput.id.in_(ids))
    ).all()
    return {oid: gid for oid, gid in rows}


def _output_asset_formats(db: Session, pairs) -> dict[int, str]:  # noqa: ANN001
    """{output_id: asset_format} for every output the given comparisons reference, in ONE query.
    Same N+1 removal as _output_generator_ids; see its docstring."""
    ids = _comparison_output_ids(pairs)
    if not ids:
        return {}
    rows = db.execute(
        select(ModelOutput.id, ModelOutput.asset_format).where(ModelOutput.id.in_(ids))
    ).all()
    return {oid: fmt for oid, fmt in rows}


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


def app_hidden_generator_ids(db: Session) -> set[int]:
    """Generators hidden from the whole app UI. Kept in the DB for internal analysis, never
    displayed. Three keys: by generator slug (config.APP_HIDDEN_GENERATOR_SLUGS — AgriGen's
    procedural-expert testers), by output source (config.APP_HIDDEN_SOURCES — xfrog / partcrafter),
    and by paradigm (config.APP_HIDDEN_PARADIGMS — retrieval + procedural_expert, which aren't what
    the arena tests). All internal-only."""
    ids: set[int] = set()
    if config.APP_HIDDEN_GENERATOR_SLUGS:
        ids |= set(
            db.execute(
                select(Generator.id).where(Generator.slug.in_(config.APP_HIDDEN_GENERATOR_SLUGS))
            ).scalars()
        )
    if config.APP_HIDDEN_SOURCES:
        # These sources are generator-dedicated (source is intrinsic to the generation pipeline),
        # so any generator with a hidden-source output is a hidden generator.
        ids |= set(
            db.execute(
                select(ModelOutput.generator_id).where(
                    ModelOutput.source.in_(config.APP_HIDDEN_SOURCES)
                )
            ).scalars()
        )
    if config.APP_HIDDEN_PARADIGMS:
        ids |= set(
            db.execute(
                select(Generator.id).where(Generator.paradigm.in_(config.APP_HIDDEN_PARADIGMS))
            ).scalars()
        )
    return ids


def vote_pool_excluded_generator_ids(db: Session) -> set[int]:
    """Generators off the HUMAN vote roster (config.ARENA_VOTE_PARADIGMS).

    Deliberately NOT part of app_hidden_generator_ids / mode_a_excluded_generator_ids: these
    generators stay fully visible — model pages, leaderboard rows, and the VLM-judge boards
    that rank them without spending human attention. The only thing they lose is a slot in the
    arena, because human votes are the scarce input and spreading them over every paradigm
    leaves every entrant provisional. See config.ARENA_VOTE_PARADIGMS for the measurement.

    An empty allowlist means "no scoping" — every generator stays in the pool.
    """
    if not config.ARENA_VOTE_PARADIGMS:
        return set()
    # `paradigm` is nullable and SQL `NOT IN` never matches NULL, so a null-paradigm generator
    # would slip INTO the pool without the explicit is_(None) arm.
    return set(
        db.execute(
            select(Generator.id).where(
                or_(
                    Generator.paradigm.is_(None),
                    Generator.paradigm.notin_(config.ARENA_VOTE_PARADIGMS),
                )
            )
        ).scalars()
    )


def mode_a_excluded_generator_ids(db: Session) -> set[int]:
    """Generators excluded from the Mode-A perceptual ranking: GT reference scans (not generative
    methods) ∪ fully-untextured generators (flat-grey-blob renders confound perceptual votes) ∪
    app-hidden internal testers (config.APP_HIDDEN_GENERATOR_SLUGS)."""
    return (
        reference_scan_generator_ids(db)
        | untextured_generator_ids(db)
        | app_hidden_generator_ids(db)
    )


def _split_provider(name: str, source: str | None) -> tuple[str, str | None]:
    """Split a trailing api/recon hosting-provider parenthetical off a generator name:
    "TRELLIS (fal)" → ("TRELLIS", "fal"). Returns (name, None) when there is no provider paren —
    non-`api:`/`recon:` sources (Plant3D (Salk), XfrogPlants (botanical), PartCrafter (part-based))
    and names without a paren are returned unchanged. Provider token kept verbatim (fal / Replicate)."""
    if source and source.startswith(("api:", "recon:")) and name.endswith(")"):
        i = name.rfind(" (")
        if i > 0 and name[i + 2 : -1]:
            return name[:i], name[i + 2 : -1]
    return name, None


def _representative_source_by_generator(db: Session) -> dict[int, str]:
    """generator_id → a source string from one of its outputs. Generators are source-dedicated
    (source is intrinsic to the generation pipeline), so any output's source is representative."""
    out: dict[int, str] = {}
    for gid, src in db.execute(select(ModelOutput.generator_id, ModelOutput.source)).all():
        if gid is not None and src and gid not in out:
            out[gid] = src
    return out


def generator_display_names(db: Session) -> dict[int, str]:
    """Map generator_id → a UNIQUE display label.

    The hosting provider (fal / Replicate) is shown ONLY when it's needed to tell entries apart —
    when the same base model runs on more than one provider (TRELLIS, Rodin text, which produce
    genuinely different meshes per provider). A model whose base name is already unique drops the
    provider as noise ("Hunyuan3D v3 (fal)" → "Hunyuan3D v3"). Names that still collide with no
    distinguishing provider (e.g. the 8 XfrogPlants variants) fall back to a slug disambiguator.
    """
    gens = db.execute(select(Generator)).scalars().all()
    src_by_gid = _representative_source_by_generator(db)
    split = {g.id: _split_provider(g.name, src_by_gid.get(g.id)) for g in gens}
    base_counts: dict[str, int] = {}
    for base, _prov in split.values():
        base_counts[base] = base_counts.get(base, 0) + 1
    # Provider shown only when the base name is ambiguous (>1 generator shares it).
    eff_name = {
        g.id: (f"{base} via {prov}" if prov and base_counts[base] > 1 else base)
        for g in gens
        for base, prov in [split[g.id]]
    }
    by_name: dict[str, list[Generator]] = {}
    for g in gens:
        by_name.setdefault(eff_name[g.id], []).append(g)
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
    db: Session,
    criterion_id: int,
    category_id: int | None = None,
    include_ties: bool = True,
    verified_only: bool = False,
    *,
    category_ids: set[int] | None = None,
) -> tuple[list[tuple[int, int]], list[int]]:
    """Decisive (winner_gen, loser_gen) pairs for a (criterion, category) scope, plus a
    parallel ballot-group key list (same length as matches) for ballot-level bootstrap
    resampling — see app/ranking.py::_bootstrap_scores.

    A 'tie' is credited as a split — one win in each direction — so ties inform
    Bradley-Terry without a separate tie parameter. 'bad' votes are excluded.
    category_id=None means the global scope (all categories).
    category_ids, when given (a SET of category ids — a "kingdom"), takes precedence over
    category_id and filters to any of those categories (an empty set yields no matches, not
    "all categories" — a kingdom with zero mapped categories must be inert, not a fallback).
    verified_only=True further restricts to votes from a session with a linked
    User (VoterSession.user_id set) — the "verified-only" leaderboard scope.
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
    if verified_only:
        stmt = stmt.where(VoterSession.user_id.is_not(None))
    if category_ids is not None:
        stmt = stmt.join(Task, Comparison.task_id == Task.id).where(
            Task.category_id.in_(category_ids)
        )
    elif category_id is not None:
        stmt = stmt.join(Task, Comparison.task_id == Task.id).where(Task.category_id == category_id)

    ref_gens = mode_a_excluded_generator_ids(db)
    matches: list[tuple[int, int]] = []
    groups: list[int] = []
    for vote, comparison in db.execute(stmt).all():
        if vote.winner == "bad":
            continue
        out_a = db.get(ModelOutput, comparison.output_a_id)
        out_b = db.get(ModelOutput, comparison.output_b_id)
        if out_a is None or out_b is None:
            continue  # dangling vote (output deleted) — not a valid comparison
        gen_a = out_a.generator_id
        gen_b = out_b.generator_id
        if gen_a in ref_gens or gen_b in ref_gens:
            continue  # GT/reference scans are not perceptual competitors (Mode-A exclusion)
        if gen_a == gen_b:
            # Both outputs came from the SAME generator ("TRELLIS vs TRELLIS"). Matchmaking now
            # refuses to serve such a pair, but historic comparisons already carry real votes.
            # A (G, G) match is a model beating itself: meaningless as a preference signal and
            # unidentifiable in Bradley-Terry, so it must never reach the fit. The rows stay in
            # the DB (audit trail) — they are just inert here. The same_paradigm() guard below
            # can't catch this: same_paradigm(p, p) is trivially true.
            continue
        if not same_paradigm(db.get(Generator, gen_a).paradigm, db.get(Generator, gen_b).paradigm):
            continue  # never rank across paradigms
        # Ballot-group key: comparisons derived from one K-wise ballot share ballot_id, so
        # their bootstrap resamples move together (not independently). Native pairwise votes
        # (ballot_id is None) each get a unique negative key — a singleton group.
        gkey = comparison.ballot_id if comparison.ballot_id is not None else -comparison.id
        if vote.winner == "a":
            matches.append((gen_a, gen_b))
            groups.append(gkey)
        elif vote.winner == "b":
            matches.append((gen_b, gen_a))
            groups.append(gkey)
        elif vote.winner == "tie" and include_ties:
            matches.append((gen_a, gen_b))
            groups.append(gkey)
            matches.append((gen_b, gen_a))
            groups.append(gkey)
    return matches, groups


def head_to_head_record(
    db: Session,
    generator_id: int,
    criterion_slug: str = "overall",
    *,
    category_ids: set[int] | None = None,
) -> list[dict]:
    """Per-opponent win/loss/tie record for one generator within its paradigm scope.

    Built from _matches_for_scope (already same-paradigm, gold/reference-excluded,
    trust-gated). Note: _matches_for_scope's `include_ties=True` mode is a Bradley-Terry
    fitting device — it splits a tie into BOTH (a,b) and (b,a) so ties inform BT without a
    separate tie parameter. That is not a display record: treating each split as its own
    game would double-count real comparisons. Here, ties use the standard 0.5-win
    convention instead: a tie counts as half a win, half a loss, and exactly ONE game.
    `games` therefore equals the true number of comparisons decided between the two
    generators (wins + losses + ties, not wins + losses + 2*ties).

    Self-generator matches (both outputs from the same generator — matchmaking doesn't
    guarantee distinct generators) are excluded: a generator is never its own opponent.

    Returns [] when the generator has no games. Sorted games desc, win% desc, opponent_id
    asc (explicit final tiebreak so equally-ranked opponents don't flap between requests).
    """
    crit = db.execute(select(Criterion).where(Criterion.slug == criterion_slug)).scalars().first()
    if crit is None:
        return []
    decisive, _ = _matches_for_scope(db, crit.id, category_ids=category_ids, include_ties=False)
    with_ties, _ = _matches_for_scope(db, crit.id, category_ids=category_ids, include_ties=True)

    def _tally(matches: list[tuple[int, int]]) -> dict[int, dict]:
        t: dict[int, dict] = {}
        for winner, loser in matches:
            if winner == loser:
                continue  # same-generator comparison — not a head-to-head
            if winner == generator_id:
                t.setdefault(loser, {"wins": 0, "losses": 0})
                t[loser]["wins"] += 1
            elif loser == generator_id:
                t.setdefault(winner, {"wins": 0, "losses": 0})
                t[winner]["losses"] += 1
        return t

    decisive_tally = _tally(decisive)
    all_tally = _tally(with_ties)

    out = []
    for opp in set(decisive_tally) | set(all_tally):
        d = decisive_tally.get(opp, {"wins": 0, "losses": 0})
        a = all_tally.get(opp, {"wins": 0, "losses": 0})
        # Each tie contributes exactly +1 to the win direction and +1 to the loss
        # direction in the split-record (`with_ties`) vs the decisive-only record.
        ties = a["wins"] - d["wins"]
        wins, losses = d["wins"], d["losses"]
        games = wins + losses + ties
        out.append(
            {
                "opponent_id": opp,
                "wins": wins,
                "losses": losses,
                "ties": ties,
                "games": games,
                "win_pct": (wins + 0.5 * ties) / games,
            }
        )
    out.sort(key=lambda r: (-r["games"], -r["win_pct"], r["opponent_id"]))
    return out


def _players_for_scope(
    db: Session, category_id: int | None = None, *, category_ids: set[int] | None = None
) -> list[int]:
    """Generators eligible for a scope's leaderboard (gold/decoy outputs excluded).

    category_ids, when given, takes precedence over category_id (see _matches_for_scope)."""
    stmt = select(ModelOutput.generator_id).where(ModelOutput.is_gold.is_(False))
    if category_ids is not None:
        stmt = stmt.join(Task, ModelOutput.task_id == Task.id).where(
            Task.category_id.in_(category_ids)
        )
    elif category_id is not None:
        stmt = stmt.join(Task, ModelOutput.task_id == Task.id).where(
            Task.category_id == category_id
        )
    ref_gens = mode_a_excluded_generator_ids(db)
    return sorted({gid for gid in db.execute(stmt).scalars().all() if gid not in ref_gens})


def finalize_rows(rows: list[dict]) -> list[dict]:
    """Add CI-grouped rank + whisker-bar geometry to leaderboard rows (shared by the
    trusted and verified boards). Rows must have numeric bt_score/bt_lower/bt_upper."""
    rows.sort(key=lambda x: x["bt_score"], reverse=True)
    # CI-grouped rank (overlapping 95% CIs share a rank), computed on the displayed
    # (rounded) bounds so the rank matches the numbers shown.
    ranks = ranking.rank_by_ci([(r["bt_lower"], r["bt_upper"]) for r in rows])
    for row, rank in zip(rows, ranks):
        row["rank"] = rank
    # CI whisker-bar geometry: position each [lower, point, upper] as a percent of the
    # column's full value span so ties are visible at a glance.
    if rows:
        lo = min(r["bt_lower"] for r in rows)
        hi = max(r["bt_upper"] for r in rows)
        span = (hi - lo) or 1.0
        for r in rows:
            r["ci_left"] = round(100.0 * (r["bt_lower"] - lo) / span, 1)
            r["ci_width"] = round(100.0 * (r["bt_upper"] - r["bt_lower"]) / span, 1)
            r["ci_point"] = round(100.0 * (r["bt_score"] - lo) / span, 1)
            # The domain the percentages above were normalized against, stamped on every row so the
            # template can LABEL the axis (BT lo–hi). Reading min/max in the template instead would
            # risk drifting from the geometry when the variant seam nests rows.
            r["ci_lo"] = round(lo, 1)
            r["ci_hi"] = round(hi, 1)
    return rows


def verified_leaderboard_rows(
    db: Session,
    criterion_slug: str = "overall",
    category: str = "all",
    *,
    category_ids: set[int] | None = None,
) -> list[dict]:
    """On-demand Bradley-Terry over VERIFIED votes only (session.user_id set). Not cached.

    category_ids, when given (a kingdom's category-id set), takes precedence over the resolved
    `category` slug — same convention as _matches_for_scope/_players_for_scope — so the
    "Verified" scope toggle stays kingdom-scoped too."""
    crit = db.execute(select(Criterion).where(Criterion.slug == criterion_slug)).scalars().first()
    if crit is None:
        return []
    category_id = None
    if category != "all":
        cat = db.execute(select(Category).where(Category.slug == category)).scalars().first()
        category_id = cat.id if cat else None
    players = _players_for_scope(db, category_id, category_ids=category_ids)
    matches, groups = _matches_for_scope(
        db, crit.id, category_id, verified_only=True, category_ids=category_ids
    )
    result = ranking.bradley_terry(players, matches, bootstrap=config.BT_BOOTSTRAP, groups=groups)
    names = generator_display_names(db)
    rows = []
    for gid in players:
        if result.n_games.get(gid, 0) <= 0:
            continue  # only generators with an actual verified game appear on the verified board
        gen = db.get(Generator, gid)
        rows.append(
            {
                "generator": names.get(gid, gen.name if gen else str(gid)),
                "kind": gen.kind if gen else "model",
                "paradigm": gen.paradigm if gen else None,
                "generator_id": gid,
                "slug": gen.slug if gen else str(gid),
                "bt_score": round(result.scores.get(gid, 0.0), 1),
                "bt_lower": round(result.lower.get(gid, 0.0), 1),
                "bt_upper": round(result.upper.get(gid, 0.0), 1),
                "n_games": result.n_games.get(gid, 0),
            }
        )
    if not rows:
        return []
    return finalize_rows(rows)


def kingdom_leaderboard_rows(
    db: Session, criterion_slug: str, category_ids: set[int] | None
) -> list[dict]:
    """On-the-fly (uncached) Bradley-Terry rows for a kingdom scope — a SET of category ids,
    which the cached `Rating` table (keyed by a single category_id) cannot represent. Mirrors
    `verified_leaderboard_rows`'s on-demand-BT shape (including the n_games>0 inclusion rule)
    but scopes by kingdom membership rather than vote verification. Caller (main._leaderboard_rows)
    still owns paradigm filtering + per-paradigm rank/CI grouping, matching the cached path."""
    crit = db.execute(select(Criterion).where(Criterion.slug == criterion_slug)).scalars().first()
    if crit is None:
        return []
    players = _players_for_scope(db, category_ids=category_ids)
    matches, groups = _matches_for_scope(db, crit.id, category_ids=category_ids)
    result = ranking.bradley_terry(players, matches, bootstrap=config.BT_BOOTSTRAP, groups=groups)
    names = generator_display_names(db)
    rows = []
    for gid in players:
        if result.n_games.get(gid, 0) <= 0:
            continue  # only generators with an actual game in this kingdom appear
        gen = db.get(Generator, gid)
        rows.append(
            {
                "generator": names.get(gid, gen.name if gen else str(gid)),
                "kind": gen.kind if gen else "model",
                "paradigm": gen.paradigm if gen else None,
                "generator_id": gid,
                "slug": gen.slug if gen else str(gid),
                "bt_score": round(result.scores.get(gid, 0.0), 1),
                "bt_lower": round(result.lower.get(gid, 0.0), 1),
                "bt_upper": round(result.upper.get(gid, 0.0), 1),
                "n_games": result.n_games.get(gid, 0),
            }
        )
    return rows


def generator_trend_series(
    db: Session,
    criterion_id: int,
    category_id: int | None = None,
    *,
    category_ids: set[int] | None = None,
    n_buckets: int = 8,
) -> dict[int, list[float | None]]:
    """Per-generator win-rate over `n_buckets` equal-width time buckets, derived from real
    `Vote.created` timestamps — the leaderboard's trend sparkline. NOT a fabricated number:
    each bucket's value is wins/games among that generator's own decisive votes landing in
    that time window (a tie splits as half a win to each side, mirroring the BT tie-credit
    convention in `_matches_for_scope`).

    Mirrors `_matches_for_scope`'s scope filters (trust gate, decisive-only, gold exclusion,
    Mode-A reference-generator exclusion, same-paradigm pairing) but keeps the vote timestamp
    instead of collapsing to a flat match list, in ONE pass over the scoped votes.

    A generator with <4 total scoped votes returns `[]` (too sparse for a meaningful trend —
    caller renders a flat baseline instead). An empty bucket carries forward the previous
    populated bucket's value; leading empty buckets stay `None` (never backfilled with a
    fabricated 0.5).
    """
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
    if category_ids is not None:
        stmt = stmt.join(Task, Comparison.task_id == Task.id).where(
            Task.category_id.in_(category_ids)
        )
    elif category_id is not None:
        stmt = stmt.join(Task, Comparison.task_id == Task.id).where(Task.category_id == category_id)

    ref_gens = mode_a_excluded_generator_ids(db)
    records: list[tuple[dt.datetime, int, int, str]] = []  # (created, gen_a, gen_b, winner)
    gen_paradigm: dict[int, str] = {}
    pairs = db.execute(stmt).all()
    out_gen = _output_generator_ids(db, pairs)
    for vote, comparison in pairs:
        if vote.winner == "bad":
            continue
        gen_a = out_gen.get(comparison.output_a_id)
        gen_b = out_gen.get(comparison.output_b_id)
        if gen_a is None or gen_b is None:
            continue  # dangling vote (output deleted)
        if gen_a in ref_gens or gen_b in ref_gens:
            continue
        if gen_a == gen_b:
            continue  # a model beating itself is not history — identical guard in
            # _matches_for_scope; same_paradigm(p, p) below is trivially true and can't catch it
        for gid in (gen_a, gen_b):
            if gid not in gen_paradigm:
                g = db.get(Generator, gid)
                gen_paradigm[gid] = g.paradigm if g else ""
        if not same_paradigm(gen_paradigm[gen_a], gen_paradigm[gen_b]):
            continue  # never blend cross-paradigm votes into one generator's trend
        records.append((vote.created, gen_a, gen_b, vote.winner))

    if not records:
        return {}
    t_min = min(r[0] for r in records)
    t_max = max(r[0] for r in records)
    span = (t_max - t_min).total_seconds() or 1.0

    def _bucket_of(ts: dt.datetime) -> int:
        frac = (ts - t_min).total_seconds() / span
        return min(int(frac * n_buckets), n_buckets - 1)

    wins: dict[int, list[float]] = {}
    games: dict[int, list[float]] = {}

    def _ensure(gid: int) -> None:
        if gid not in wins:
            wins[gid] = [0.0] * n_buckets
            games[gid] = [0.0] * n_buckets

    for created, gen_a, gen_b, winner in records:
        b = _bucket_of(created)
        _ensure(gen_a)
        _ensure(gen_b)
        if winner == "a":
            wins[gen_a][b] += 1.0
            games[gen_a][b] += 1.0
            games[gen_b][b] += 1.0
        elif winner == "b":
            wins[gen_b][b] += 1.0
            games[gen_b][b] += 1.0
            games[gen_a][b] += 1.0
        elif winner == "tie":
            wins[gen_a][b] += 0.5
            wins[gen_b][b] += 0.5
            games[gen_a][b] += 1.0
            games[gen_b][b] += 1.0

    out: dict[int, list[float | None]] = {}
    for gid, g_games in games.items():
        if sum(g_games) < 4:
            out[gid] = []  # too sparse — render a flat baseline, not a noisy trend
            continue
        series: list[float | None] = []
        prev: float | None = None
        for b in range(n_buckets):
            if g_games[b] > 0:
                prev = round(wins[gid][b] / g_games[b], 3)
            series.append(prev)
        out[gid] = series
    return out


def recompute_scope(
    db: Session, criterion: Criterion, category_id: int | None, commit: bool = True
) -> dict:
    """Refit Bradley-Terry for one (criterion, category) scope and cache Rating rows."""
    matches, groups = _matches_for_scope(db, criterion.id, category_id)
    players = sorted(set(_players_for_scope(db, category_id)) | {p for m in matches for p in m})
    result = ranking.bradley_terry(players, matches, bootstrap=config.BT_BOOTSTRAP, groups=groups)
    for gid in players:
        rating = get_or_create_rating(db, gid, criterion.id, category_id)
        rating.bt_score = result.scores.get(gid, ranking.BT_BASE)
        rating.bt_lower = result.lower.get(gid, ranking.BT_BASE)
        rating.bt_upper = result.upper.get(gid, ranking.BT_BASE)
        rating.n_games = int(result.n_games.get(gid, 0))
    if commit:
        db.commit()
    return {"matches": len(matches), "players": len(players)}


def recompute_kingdom_scope(
    db: Session,
    criterion: Criterion,
    kingdom: str,
    category_ids: set[int] | None,
    *,
    commit: bool = False,
) -> dict:
    """Refit Bradley-Terry for a kingdom scope (a SET of categories) and cache KingdomRating
    rows. Mirrors `kingdom_leaderboard_rows`'s on-the-fly BT + n_games>0 inclusion rule, so the
    cache and the fallback are byte-for-byte the same computation.

    Unlike `recompute_scope` (which get-or-creates and keeps every historical player forever),
    this delete-then-reinserts the scope's rows every time: a kingdom's player set can shrink
    between recomputes (a generator's only in-kingdom game could later fall out of the decisive
    record), and the cache must not keep serving a stale n_games>0 row for a player that no
    longer has one.
    """
    players = _players_for_scope(db, category_ids=category_ids)
    matches, groups = _matches_for_scope(db, criterion.id, category_ids=category_ids)
    result = ranking.bradley_terry(players, matches, bootstrap=config.BT_BOOTSTRAP, groups=groups)
    db.execute(
        delete(KingdomRating).where(
            KingdomRating.kingdom == kingdom, KingdomRating.criterion_id == criterion.id
        )
    )
    n = 0
    for gid in players:
        n_games = int(result.n_games.get(gid, 0))
        if n_games <= 0:
            continue  # only generators with an actual in-kingdom game are cached
        db.add(
            KingdomRating(
                generator_id=gid,
                kingdom=kingdom,
                criterion_id=criterion.id,
                bt_score=result.scores.get(gid, ranking.BT_BASE),
                bt_lower=result.lower.get(gid, ranking.BT_BASE),
                bt_upper=result.upper.get(gid, ranking.BT_BASE),
                n_games=n_games,
            )
        )
        n += 1
    if commit:
        db.commit()
    return {"matches": len(matches), "players": n}


def cached_kingdom_leaderboard_rows(
    db: Session, criterion_slug: str, kingdom: str
) -> list[dict] | None:
    """Read cached `KingdomRating` rows for (kingdom, criterion), shaped exactly like
    `kingdom_leaderboard_rows`'s on-the-fly output. Returns None on a cache MISS (no rows yet
    for this scope) — the caller's signal to fall back to the on-the-fly path, which must stay
    correct even before the first `/admin/recompute`."""
    crit = db.execute(select(Criterion).where(Criterion.slug == criterion_slug)).scalars().first()
    if crit is None:
        return None
    cached = (
        db.execute(
            select(KingdomRating).where(
                KingdomRating.kingdom == kingdom, KingdomRating.criterion_id == crit.id
            )
        )
        .scalars()
        .all()
    )
    if not cached:
        return None
    names = generator_display_names(db)
    rows = []
    for r in cached:
        gen = db.get(Generator, r.generator_id)
        if gen is None:
            continue  # stale cache row (generator deleted); skip rather than crash
        rows.append(
            {
                "generator": names.get(r.generator_id, gen.name),
                "kind": gen.kind,
                "paradigm": gen.paradigm,
                "generator_id": r.generator_id,
                "slug": gen.slug,
                "bt_score": round(r.bt_score, 1),
                "bt_lower": round(r.bt_lower, 1),
                "bt_upper": round(r.bt_upper, 1),
                "n_games": r.n_games,
            }
        )
    return rows


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
    """Recompute every (criterion × {global + each category}) leaderboard scope, plus every
    (criterion × kingdom) scope for the kingdom leaderboard cache."""
    criteria = db.execute(select(Criterion)).scalars().all()
    categories = db.execute(select(Category)).scalars().all()
    n_scopes = 0
    for criterion in criteria:
        recompute_scope(db, criterion, category_id=None, commit=False)
        n_scopes += 1
        for cat in categories:
            recompute_scope(db, criterion, category_id=cat.id, commit=False)
            n_scopes += 1
        for kingdom in kingdoms.KINGDOMS:
            cat_ids = kingdoms.category_ids_for_kingdom(db, kingdom)
            if not cat_ids:
                continue  # kingdom has no mapped categories (yet) -> nothing to cache
            recompute_kingdom_scope(db, criterion, kingdom, cat_ids, commit=False)
            n_scopes += 1
    db.commit()
    return {
        "status": "ok",
        "scopes": n_scopes,
        "criteria": len(criteria),
        "categories": len(categories),
    }


def _judge_matches_for_scope(
    db: Session,
    criterion_id: int,
    view_condition: str,
    include_ties: bool = True,
    *,
    category_ids: set[int] | None = None,
) -> list[tuple[int, int]]:
    """Decisive (winner_gen, loser_gen) pairs from JudgeVote for one (criterion, condition).
    Tie → split both directions; bad excluded. Mirrors _matches_for_scope (human).

    category_ids, when given (a kingdom's category-id set), restricts to JudgeVotes whose
    task belongs to one of those categories — same "kingdom" convention as _matches_for_scope,
    but JudgeVote carries task_id directly (no Comparison join needed)."""
    stmt = select(JudgeVote).where(
        JudgeVote.criterion_id == criterion_id,
        JudgeVote.view_condition == view_condition,
    )
    if category_ids is not None:
        stmt = stmt.join(Task, JudgeVote.task_id == Task.id).where(
            Task.category_id.in_(category_ids)
        )
    ref_gens = mode_a_excluded_generator_ids(db)
    matches: list[tuple[int, int]] = []
    for jv in db.execute(stmt).scalars():
        if jv.winner == "bad":
            continue
        out_a = db.get(ModelOutput, jv.output_a_id)
        out_b = db.get(ModelOutput, jv.output_b_id)
        if out_a is None or out_b is None:
            continue  # dangling vote (output deleted) — not a valid comparison
        gen_a = out_a.generator_id
        gen_b = out_b.generator_id
        if gen_a in ref_gens or gen_b in ref_gens:
            continue  # GT/reference scans are not perceptual competitors (Mode-A exclusion)
        if gen_a == gen_b:
            # Both outputs came from the SAME generator ("TRELLIS vs TRELLIS"). A (G, G) match
            # is a model beating itself: meaningless as a preference signal and unidentifiable
            # in Bradley-Terry, so it must never reach the fit. The judge_vote rows stay in the
            # DB (audit trail) — they are just inert here. The same_paradigm() guard below can't
            # catch this: same_paradigm(p, p) is trivially true.
            continue
        if not same_paradigm(db.get(Generator, gen_a).paradigm, db.get(Generator, gen_b).paradigm):
            continue  # never rank across paradigms
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
    """Refit Bradley-Terry over JudgeVote for (criterion, condition); cache JudgeRating.

    Delete-then-reinsert per scope, same rationale as `recompute_kingdom_judge_scope`: the
    player set SHRINKS (a generator with no non-gold outputs left — hidden, deleted, or
    reclassified — drops out of `_players_for_scope`). Upserting only the current players left
    those rows stranded with whatever score the fit produced when they last qualified, and the
    board still read them. That is how 40 rows kept pre-fix scores (-6403 .. +60292, including
    a visible TRELLIS at 18029) across every later recompute. A cached rating must not outlive
    the fit that produced it."""
    db.query(JudgeRating).filter(
        JudgeRating.criterion_id == criterion.id,
        JudgeRating.view_condition == view_condition,
        JudgeRating.category_id.is_(None),
    ).delete(synchronize_session=False)
    db.flush()
    matches = _judge_matches_for_scope(db, criterion.id, view_condition)
    judge_model = _judge_model_for_scope(db, criterion.id, view_condition)
    players = sorted(set(_players_for_scope(db, None)) | {p for m in matches for p in m})
    # VLM-judge fit: evidence-scaled center prior (see config.JUDGE_PRIOR_FRAC) keeps the
    # disconnected-by-construction, near-deterministic judge graph finite. Human boards don't
    # pass it (they keep the unpenalized MLE).
    result = ranking.bradley_terry(
        players,
        matches,
        bootstrap=config.BT_BOOTSTRAP,
        prior_frac=config.JUDGE_PRIOR_FRAC,
        prior_floor=config.JUDGE_PRIOR_FLOOR,
    )
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


def recompute_kingdom_judge_scope(
    db: Session,
    criterion: Criterion,
    kingdom: str,
    view_condition: str,
    category_ids: set[int] | None,
    *,
    commit: bool = False,
) -> dict:
    """Refit VLM-judge Bradley-Terry for a kingdom scope and cache `KingdomJudgeRating` rows —
    the judge-board analog of `recompute_kingdom_scope`. Mirrors
    `kingdom_judge_leaderboard_rows`'s on-the-fly BT + n_games>0 inclusion rule, so the cache and
    the fallback are byte-for-byte the same computation. Delete-then-reinsert per scope, same
    rationale as `recompute_kingdom_scope` (a kingdom's judge player set can shrink)."""
    players = _players_for_scope(db, category_ids=category_ids)
    matches = _judge_matches_for_scope(db, criterion.id, view_condition, category_ids=category_ids)
    # VLM-judge fit: evidence-scaled center prior (see config.JUDGE_PRIOR_FRAC) keeps the
    # disconnected-by-construction, near-deterministic judge graph finite. Human boards don't
    # pass it (they keep the unpenalized MLE).
    result = ranking.bradley_terry(
        players,
        matches,
        bootstrap=config.BT_BOOTSTRAP,
        prior_frac=config.JUDGE_PRIOR_FRAC,
        prior_floor=config.JUDGE_PRIOR_FLOOR,
    )
    db.execute(
        delete(KingdomJudgeRating).where(
            KingdomJudgeRating.kingdom == kingdom,
            KingdomJudgeRating.criterion_id == criterion.id,
            KingdomJudgeRating.view_condition == view_condition,
        )
    )
    n = 0
    for gid in players:
        n_games = int(result.n_games.get(gid, 0))
        if n_games <= 0:
            continue  # only generators with an actual in-kingdom judge game are cached
        db.add(
            KingdomJudgeRating(
                generator_id=gid,
                kingdom=kingdom,
                criterion_id=criterion.id,
                view_condition=view_condition,
                bt_score=result.scores.get(gid, ranking.BT_BASE),
                bt_lower=result.lower.get(gid, ranking.BT_BASE),
                bt_upper=result.upper.get(gid, ranking.BT_BASE),
                n_games=n_games,
            )
        )
        n += 1
    if commit:
        db.commit()
    return {"matches": len(matches), "players": n}


def cached_kingdom_judge_leaderboard_rows(
    db: Session, criterion_slug: str, view_condition: str, kingdom: str
) -> list[dict] | None:
    """Read cached `KingdomJudgeRating` rows for (kingdom, criterion, view_condition), shaped
    exactly like `kingdom_judge_leaderboard_rows`'s on-the-fly output. Returns None on a cache
    MISS — the caller's signal to fall back to the on-the-fly path."""
    crit = db.execute(select(Criterion).where(Criterion.slug == criterion_slug)).scalars().first()
    if crit is None:
        return None
    cached = (
        db.execute(
            select(KingdomJudgeRating).where(
                KingdomJudgeRating.kingdom == kingdom,
                KingdomJudgeRating.criterion_id == crit.id,
                KingdomJudgeRating.view_condition == view_condition,
            )
        )
        .scalars()
        .all()
    )
    if not cached:
        return None
    names = generator_display_names(db)
    rows = []
    for r in cached:
        gen = db.get(Generator, r.generator_id)
        if gen is None:
            continue  # stale cache row (generator deleted); skip rather than crash
        rows.append(
            {
                "generator": names.get(r.generator_id, gen.name),
                "kind": gen.kind,
                "paradigm": gen.paradigm,
                "bt_score": round(r.bt_score, 1),
                "bt_lower": round(r.bt_lower, 1),
                "bt_upper": round(r.bt_upper, 1),
                "n_games": r.n_games,
            }
        )
    return rows


def recompute_judge_all(db: Session, view_condition: str = "multi4") -> dict:
    """Recompute the VLM leaderboard for every criterion under one view condition, plus every
    (criterion × kingdom) scope for the kingdom judge-board cache."""
    criteria = db.execute(select(Criterion)).scalars().all()
    for criterion in criteria:
        recompute_judge_scope(db, criterion, view_condition, commit=False)
        for kingdom in kingdoms.KINGDOMS:
            cat_ids = kingdoms.category_ids_for_kingdom(db, kingdom)
            if not cat_ids:
                continue  # kingdom has no mapped categories (yet) -> nothing to cache
            recompute_kingdom_judge_scope(
                db, criterion, kingdom, view_condition, cat_ids, commit=False
            )
    db.commit()
    return {"status": "ok", "view_condition": view_condition, "criteria": len(criteria)}


def kingdom_judge_leaderboard_rows(
    db: Session, criterion_slug: str, view_condition: str, category_ids: set[int] | None
) -> list[dict]:
    """On-the-fly (uncached) VLM-judge Bradley-Terry rows for a kingdom scope — mirrors
    `kingdom_leaderboard_rows` (human) but over JudgeVote instead of Vote, since the cached
    `JudgeRating` table is likewise keyed by a single category_id and cannot represent a SET
    of categories. Caller (main._kingdom_judge_leaderboard_rows) still owns per-paradigm
    rank/CI grouping, matching the cached judge path's shape."""
    crit = db.execute(select(Criterion).where(Criterion.slug == criterion_slug)).scalars().first()
    if crit is None:
        return []
    players = _players_for_scope(db, category_ids=category_ids)
    matches = _judge_matches_for_scope(db, crit.id, view_condition, category_ids=category_ids)
    # VLM-judge fit: evidence-scaled center prior (see config.JUDGE_PRIOR_FRAC) keeps the
    # disconnected-by-construction, near-deterministic judge graph finite. Human boards don't
    # pass it (they keep the unpenalized MLE).
    result = ranking.bradley_terry(
        players,
        matches,
        bootstrap=config.BT_BOOTSTRAP,
        prior_frac=config.JUDGE_PRIOR_FRAC,
        prior_floor=config.JUDGE_PRIOR_FLOOR,
    )
    names = generator_display_names(db)
    rows = []
    for gid in players:
        if result.n_games.get(gid, 0) <= 0:
            continue  # only generators with an actual judge game in this kingdom appear
        gen = db.get(Generator, gid)
        rows.append(
            {
                "generator": names.get(gid, gen.name if gen else str(gid)),
                "kind": gen.kind if gen else "model",
                "paradigm": gen.paradigm if gen else None,
                "bt_score": round(result.scores.get(gid, 0.0), 1),
                "bt_lower": round(result.lower.get(gid, 0.0), 1),
                "bt_upper": round(result.upper.get(gid, 0.0), 1),
                "n_games": result.n_games.get(gid, 0),
            }
        )
    return rows


# Memoized tier_perceptual_ranking output, keyed by (criterion, condition) → (vote_signature,
# result). The board's bootstrapped BT is the /difficulty page's dominant cost and only changes
# when the relevant judge votes change, so a repeat load is served from here.
_perceptual_cache: dict[tuple[str, str], tuple[tuple, list[dict]]] = {}


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

    # Cache signature: the ranking only changes when the relevant judge votes change. Cheap to
    # compute (count + max id), so a repeat load returns the memoized result instantly.
    sig = tuple(
        db.execute(
            select(func.count(JudgeVote.id), func.max(JudgeVote.id)).where(
                JudgeVote.criterion_id == crit.id, JudgeVote.view_condition == view_condition
            )
        ).one()
    )
    ckey = (criterion_slug, view_condition)
    hit = _perceptual_cache.get(ckey)
    if hit is not None and hit[0] == sig:
        return hit[1]

    tier_by_task = {td.task_id: td.tier for td in db.execute(select(TaskDifficulty)).scalars()}
    excluded = mode_a_excluded_generator_ids(db)
    names = generator_display_names(db)
    # Batch what was an N+1 over every judge vote (~4 db.get each): output→generator and
    # generator→paradigm, one query apiece.
    gen_by_out = dict(db.execute(select(ModelOutput.id, ModelOutput.generator_id)).all())
    paradigm_by_gen = {g.id: g.paradigm for g in db.execute(select(Generator)).scalars()}

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
        gen_a = gen_by_out.get(jv.output_a_id)
        gen_b = gen_by_out.get(jv.output_b_id)
        if gen_a is None or gen_b is None:
            continue  # dangling vote (output deleted) — not a valid comparison
        if gen_a in excluded or gen_b in excluded:
            continue
        if not same_paradigm(paradigm_by_gen.get(gen_a), paradigm_by_gen.get(gen_b)):
            continue  # never rank across paradigms
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
            # bootstrap=0: this board renders only bt_score + n_games (no CI columns), so the
            # 200-resample bootstrap — the dominant cost, ~24s cold across all tiers — is pure
            # waste here. The point estimate (result.scores) is bootstrap-independent.
            result = ranking.bradley_terry(players, matches, bootstrap=0)
            rows = [
                {
                    "generator": names.get(p, str(p)),
                    "paradigm": paradigm_by_gen.get(p),
                    "bt_score": round(result.scores.get(p, ranking.BT_BASE), 1),
                    "n_games": int(result.n_games.get(p, 0)),
                }
                for p in players
            ]
            # Rank WITHIN each paradigm group, mirroring the human/judge leaderboards —
            # matches never cross paradigms (I3 gate), but a tier can still host multiple
            # within-paradigm BT components that must not share one flat ranking (I3b).
            rows.sort(key=lambda r: (r["paradigm"], -r["bt_score"]))
            rank_counters: dict[str, int] = {}
            for r in rows:
                rank_counters[r["paradigm"]] = rank_counters.get(r["paradigm"], 0) + 1
                r["rank"] = rank_counters[r["paradigm"]]
        out.append({"tier": tier, "rows": rows, "n_matches": len(matches)})
    _perceptual_cache[ckey] = (sig, out)
    return out


def compute_significance(
    db: Session,
    criterion_slug: str = "overall",
    category_id: int | None = None,
    *,
    category_ids: set[int] | None = None,
    rated_only: bool = True,
) -> dict:
    """Pairwise P(A ranks above B) for a scope — "is A *meaningfully* ahead of B?".

    category_ids, when given (a kingdom's category-id set), takes precedence over category_id —
    same convention as _matches_for_scope/_players_for_scope.

    rated_only (default True): include only generators that actually appear in a comparison.
    A never-voted generator has no significance signal — it sits at the default BT_BASE and
    floods the forest plot + P(A>B) matrix with meaningless rows. `n_unrated` reports how many
    were hidden so the page can offer a "show all" toggle."""
    criterion = (
        db.execute(select(Criterion).where(Criterion.slug == criterion_slug)).scalars().first()
    )
    if criterion is None:
        return {"status": "no-such-criterion"}
    matches, groups = _matches_for_scope(db, criterion.id, category_id, category_ids=category_ids)
    voted = {p for m in matches for p in m}
    all_players = set(_players_for_scope(db, category_id, category_ids=category_ids)) | voted
    n_unrated = len(all_players - voted)
    players = sorted(voted if rated_only else all_players)
    result = ranking.significance_matrix(
        players, matches, bootstrap=config.BT_BOOTSTRAP, groups=groups
    )
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
        "n_unrated": n_unrated,  # never-voted generators hidden unless rated_only=False
        "n_total": len(all_players),
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
    fmt_of = _output_asset_formats(db, rows)
    a = b = tie = bad = cross_format = n = 0
    fmt_wins: dict[str, int] = defaultdict(int)
    fmt_games: dict[str, int] = defaultdict(int)
    for vote, comp in rows:
        fmt_a = fmt_of.get(comp.output_a_id)
        fmt_b = fmt_of.get(comp.output_b_id)
        if fmt_a is None or fmt_b is None:
            continue  # dangling vote (output deleted) — not a valid comparison, mirrors the
            # identical guard in _matches_for_scope
        n += 1
        is_cross = fmt_a != fmt_b
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
            win_fmt, lose_fmt = (fmt_a, fmt_b) if vote.winner == "a" else (fmt_b, fmt_a)
            fmt_wins[win_fmt] += 1
            fmt_games[win_fmt] += 1
            fmt_games[lose_fmt] += 1
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


def firm_status(n_games: int) -> dict:
    """Votes-until-firm signal for a leaderboard row. `firm` once n_games >= FIRM_VOTE_THRESHOLD,
    else a countdown label so a low-vote rank reads as evaluation-in-progress, not settled."""
    if n_games >= FIRM_VOTE_THRESHOLD:
        return {"firm": True, "label": "firm"}
    remaining = FIRM_VOTE_THRESHOLD - n_games
    unit = "vote" if remaining == 1 else "votes"
    return {"firm": False, "label": f"{remaining} more {unit} → firm"}


def modality_hub_cards(
    rows_fn: Callable[[str], list[dict]], modalities: Iterable[str]
) -> list[dict]:
    """One hub card per VISIBLE modality (generation paradigm), in `paradigms.PARADIGMS` order.

    The leaderboard's spine is the modality: every board ranks exactly ONE paradigm (BT scores
    across paradigms come from disconnected match pools and are not comparable), so the landing
    page is a hub of modalities rather than a merged cross-paradigm ranking.

    `modalities` is the set of modalities that EXIST as a public surface (the caller passes the
    roster's paradigms — see main._visible_modalities); every one of them gets a card, whether or
    not anyone has voted on it. It used to be "a modality earns a card only if it has ≥1 rated
    entrant", which on production data (votes in image_recon only) rendered ONE card and left
    /leaderboard/text_native, /leaderboard/procedural_llm and /leaderboard/agentic reachable only
    by typing the URL — an unvoted modality silently vanished instead of reading as
    evaluation-in-progress, and the HTML disagreed with /api/leaderboard about which boards exist.
    A modality with no rated entrant now carries an honest empty state (`rated_count == 0`), still
    clickable. Iteration is over `paradigms.PARADIGMS` so the order is the registry's regardless of
    the caller's; app-hidden paradigms never get a card, belt-and-braces with the generator-level
    hiding in app_hidden_generator_ids().

    `rows_fn(paradigm)` returns EVERY entrant of that paradigm for the current scope, rated or not
    (the /leaderboard route passes a closure so scoping stays in one place). The card's top-3 is
    re-ranked over the RATED subset alone (a fresh 1..N — an unrated entrant carries only the
    default prior BT and has nothing to rank, so it must not push the rated leader to rank 2).

    `firm` is ALL-rated-entrants-are-firm, never `any()`: one model over the threshold used to
    stamp the card "firm" while the board it links to showed most rows still counting down
    ("N more votes → firm"). `firm_count`/`rated_count` let the card state the same thing the
    board does ("1 of 13 firm")."""
    wanted = set(modalities)
    cards: list[dict] = []
    for p in paradigms.PARADIGMS:
        if p not in wanted or p in config.APP_HIDDEN_PARADIGMS:
            continue
        rows = rows_fn(p)
        rated = [r for r in rows if r.get("n_games", 0) > 0]
        # COPIES: finalize_rows() rewrites rank/ci_* in place, and `rows` belongs to the caller.
        top = finalize_rows([dict(r) for r in rated])[:3]
        firm_count = sum(1 for r in rated if r.get("n_games", 0) >= FIRM_VOTE_THRESHOLD)
        cards.append(
            {
                "paradigm": p,
                "display": paradigms.DISPLAY_NAMES.get(p, p),
                "what": paradigms.WHAT_THIS_MEASURES.get(p, ""),
                "top": top,
                "model_count": len(rows),  # entrants in this modality (rated or not)
                "rated_count": len(rated),
                "firm_count": firm_count,
                "firm": bool(rated) and firm_count == len(rated),
            }
        )
    return cards


def coverage_summary(db: Session, category_ids: set[int] | None = None) -> dict:
    """Per-generator + per-task coverage & vote-count disclosure (governance transparency).

    Read-only aggregate powering /coverage and /api/coverage.json. Surfaces, per generator,
    how many votes/outputs/tasks back its rank (+ a firm/provisional confidence flag), and per
    task, how thinly or richly it is covered — the data the post-2025 "leaderboard illusion"
    critique asks every arena to publish, and the substrate for a future phylogenetic map.

    `category_ids` (when given, e.g. a kingdom's mapped category set) restricts the per-task
    rows to that set; the per-generator rows stay global (a generator's overall vote/output
    count is a cross-kingdom fact about the generator, not a kingdom-scoped one)."""
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
    _tasks_stmt = select(Task).where(Task.active.is_(True))
    if category_ids is not None:
        _tasks_stmt = _tasks_stmt.where(Task.category_id.in_(category_ids))
    for t in db.execute(_tasks_stmt).scalars().all():
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
        # Mode-C: this task has a literature-sourced trait rubric, and the mean
        # botanical-accuracy over its scored (calibrated-class) outputs.
        has_rubric = bool(
            db.execute(
                select(func.count(TraitRubric.id)).where(TraitRubric.task_id == t.id)
            ).scalar_one()
        )
        mode_c_accuracy = None
        if out_ids:
            accs = [
                ts.botanical_accuracy
                for ts in db.execute(
                    select(TraitScore).where(TraitScore.output_id.in_(out_ids))
                ).scalars()
                if ts.botanical_accuracy is not None
            ]
            if accs:
                mode_c_accuracy = round(sum(accs) / len(accs), 3)
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
                "has_rubric": has_rubric,
                "mode_c_accuracy": mode_c_accuracy,
            }
        )
    task_rows.sort(key=lambda r: (-r["outputs"], r["task"]))

    # Count non-gold outputs by paradigm
    by_paradigm: dict[str, int] = {}
    for o in db.execute(select(ModelOutput).where(ModelOutput.is_gold.is_(False))).scalars():
        g = db.get(Generator, o.generator_id)
        key = g.paradigm if g else ""
        by_paradigm[key] = by_paradigm.get(key, 0) + 1

    return {"generators": gen_rows, "tasks": task_rows, "by_paradigm": by_paradigm}


def _relative_time(value: dt.datetime | None) -> str:
    """Render a datetime as a short relative string ("2h ago", "just now", "3d ago").

    No existing relative-time helper was found in app/ or templates to reuse (leaderboard
    templates render raw timestamps client-side), so this is a small standalone formatter.
    `value` may be naive (as read back from SQLite — stored via `_utcnow()`, which is UTC)
    or tz-aware; both are normalized to UTC before diffing against `dt.datetime.now(utc)`.
    """
    if value is None:
        return "—"
    now = dt.datetime.now(dt.timezone.utc)
    v = value if value.tzinfo is not None else value.replace(tzinfo=dt.timezone.utc)
    secs = max(0.0, (now - v).total_seconds())
    if secs < 60:
        return "just now"
    mins = int(secs // 60)
    if mins < 60:
        return f"{mins}m ago"
    hours = int(mins // 60)
    if hours < 24:
        return f"{hours}h ago"
    days = int(hours // 24)
    return f"{days}d ago"


def kingdom_scope_stats(db: Session, kingdom: str) -> dict | None:
    """Cheap kingdom-scoped counts for the scope-bar stats strip (`.b3d-kstats`):
    active-task count, vote count, and a relative-time string for the latest vote in scope.

    `kingdom="all"` (or unmapped) scopes over every category — reuses
    `matchmaking.total_votes` for the vote count per the design brief. A specific kingdom
    joins Vote -> Comparison -> Task on indexed columns (Task.category_id, Comparison.task_id,
    Task.active) — single COUNT / MAX queries, no row materialization. Returns None on any
    error; callers must never let a stats failure break a page.
    """
    try:
        category_ids = kingdoms.category_ids_for_kingdom(db, kingdom)

        tasks_stmt = select(func.count(Task.id)).where(Task.active.is_(True))
        if category_ids is not None:
            tasks_stmt = tasks_stmt.where(Task.category_id.in_(category_ids))
        tasks = db.execute(tasks_stmt).scalar_one()

        if category_ids is None:
            votes = matchmaking.total_votes(db)
            latest = db.execute(select(func.max(Vote.created))).scalar_one()
        else:
            votes_stmt = (
                select(func.count(Vote.id))
                .select_from(Vote)
                .join(Comparison, Vote.comparison_id == Comparison.id)
                .join(Task, Comparison.task_id == Task.id)
                .where(Task.category_id.in_(category_ids))
            )
            latest_stmt = (
                select(func.max(Vote.created))
                .select_from(Vote)
                .join(Comparison, Vote.comparison_id == Comparison.id)
                .join(Task, Comparison.task_id == Task.id)
                .where(Task.category_id.in_(category_ids))
            )
            votes = db.execute(votes_stmt).scalar_one()
            latest = db.execute(latest_stmt).scalar_one()

        return {"tasks": tasks, "votes": votes, "updated": _relative_time(latest)}
    except Exception:
        return None


MODE_C_KAPPA_BAR = 0.6
MODE_C_MIN_N = 20

# A pair is dropped from the calibration when the VLM verdict is one of these: the VLM made
# no scoreable call, so there is nothing about the score to calibrate. This is ASYMMETRIC on
# purpose — a pair is kept when the HUMAN says not_assessable but the VLM made a scoreable
# call (present_*/absent). That case is the VLM's most dangerous error: it "sees" a trait on
# a model that doesn't depict it (e.g. habit on a single-fruit tomato), and scoring will
# COUNT that over-read verdict. Dropping it (as a symmetric "either side na" rule would)
# hides the failure and inflates kappa. Scoring drops VLM not_assessable from the accuracy
# denominator (recompute_trait_scores); this keeps kappa measuring exactly the verdicts the
# score is built from.
KAPPA_EXCLUDED_VERDICTS = {"not_assessable"}


def load_scopes(db: Session, judge_model: str | None = None) -> dict[int, dict]:
    """{output_id: {"is_plant": bool, "visible_parts": [str]}} from ModelScope, for the given
    judge_model (defaults to all). Empty when no scope pass has run — callers then fail open."""
    import json

    scopes: dict[int, dict] = {}
    for s in db.execute(select(ModelScope)).scalars():
        if judge_model is not None and s.judge_model != judge_model:
            continue
        try:
            parts = json.loads(s.parts_json or "[]")
        except (ValueError, TypeError):
            parts = []
        scopes[s.output_id] = {"is_plant": bool(s.is_plant), "visible_parts": parts}
    return scopes


def procedural_scorecard(db: Session, judge_model: str | None = None) -> list[dict]:
    """Per-model scorecard for the procedural_llm paradigm (LLMs authoring Blender-Python).
    Existing data only. pass@1 = valid/attempts from CommissionAttempt (status 'ok').
    Morphology fidelity = present_correct / scope-assessable non-na TraitVerdicts on the
    generator's commissioned outputs — EXPERIMENTAL/uncalibrated (Mode-C kappa-gate open).
    One row per procedural_llm generator, ranked by pass@1 desc (tiebreak morph_fidelity)."""
    import json
    import statistics

    if judge_model is None:
        from . import judge

        judge_model = judge.JUDGE_MODEL
    gens = (
        db.execute(select(Generator).where(Generator.paradigm == "procedural_llm")).scalars().all()
    )
    if not gens:
        return []
    scopes = load_scopes(db)
    rows: list[dict] = []
    for gen in gens:
        attempts = (
            db.execute(
                select(CommissionAttempt).where(
                    CommissionAttempt.generator_id == gen.id,
                    CommissionAttempt.protocol != "legacy",
                )
            )
            .scalars()
            .all()
        )
        n_attempts = len(attempts)
        if n_attempts == 0:
            # No attempt under the reported protocol — a retired model, or one measured only under
            # the old harness (legacy rows are excluded above). It has not been measured on this
            # board; showing it would be a phantom 0/0 row that reads as "scored zero", not
            # "not measured". Skip it entirely.
            continue
        ok = [a for a in attempts if a.status == "ok"]
        n_valid = len(ok)
        # TWO numbers, and the board shows both. pass@1 is the UNAIDED script — the honest version
        # of what this column always claimed to be. pass@repair is after up to 2 rounds with the
        # traceback handed back, which is how people actually use these models. Reporting only the
        # first published grok-4.20 at 2/17 on cells that rerun ~50/50; reporting only the second
        # would hide that these models write Blender that does not run.
        n_oneshot = len([a for a in attempts if a.status_oneshot == "ok"])
        pass_at_1 = (n_oneshot / n_attempts) if n_attempts else 0.0
        pass_repair = (n_valid / n_attempts) if n_attempts else 0.0
        repaired = [a.rounds for a in ok if a.rounds > 1]
        mean_rounds = (sum(a.rounds for a in attempts) / n_attempts) if n_attempts else 0.0

        verts: list[int] = []
        for a in ok:
            try:
                verts.append(int(json.loads(a.mesh_stats_json or "{}").get("vertices", 0)))
            except (ValueError, TypeError):
                continue
        median_verts = int(statistics.median(verts)) if verts else 0

        out_ids = (
            db.execute(
                select(ModelOutput.id).where(
                    ModelOutput.generator_id == gen.id,
                    ModelOutput.source == "commissioned",
                )
            )
            .scalars()
            .all()
        )
        morph_correct = 0
        morph_assessable = 0
        if out_ids:
            verdicts = (
                db.execute(
                    select(TraitVerdict).where(
                        TraitVerdict.output_id.in_(out_ids),
                        TraitVerdict.judge_model == judge_model,
                    )
                )
                .scalars()
                .all()
            )
            for v in verdicts:
                if v.verdict == "not_assessable":
                    continue
                if not is_assessable(
                    scopes.get(v.output_id),
                    {"key": v.trait_key, "trait_class": v.trait_class},
                ):
                    continue
                morph_assessable += 1
                if v.verdict == "present_correct":
                    morph_correct += 1
        morph_fidelity = (morph_correct / morph_assessable) if morph_assessable else None

        rows.append(
            {
                "model": gen.name,
                "attempts": n_attempts,
                "valid": n_valid,
                "pass_at_1": pass_at_1,  # unaided
                "n_oneshot": n_oneshot,
                "pass_repair": pass_repair,  # after <=2 repair rounds
                "n_repaired": len(repaired),  # passed only because it got its traceback back
                "mean_rounds": mean_rounds,
                "morph_correct": morph_correct,
                "morph_assessable": morph_assessable,
                "morph_fidelity": morph_fidelity,
                "median_verts": median_verts,
                "n": n_attempts,
            }
        )
    rows.sort(
        key=lambda r: (
            r["pass_repair"],
            r["pass_at_1"],
            r["morph_fidelity"] if r["morph_fidelity"] is not None else -1.0,
        ),
        reverse=True,
    )
    return rows


def calibration_pairs_by_class(human_labels, stored, scope_by_output=None):
    """human_labels: iterable of (output_id, trait_key, trait_class, human_verdict).
    stored: {(output_id, trait_key): vlm_verdict}. scope_by_output: optional {output_id: scope}
    from load_scopes — when given, a pair is dropped if the trait is not assessable on that
    model's depicted scope (e.g. habit on a single-fruit model), matching what scoring counts.
    Returns (by_class, stats). A pair whose VLM verdict is not_assessable is dropped (nothing
    scoreable); a pair whose HUMAN verdict is not_assessable but whose VLM verdict is scoreable
    is KEPT (a VLM over-read on an assessable trait must count against agreement). Single-sourced
    so the ingest dry-run preview and the committed calibration agree."""
    by_class: dict[str, tuple[list, list]] = {}
    unmatched = 0
    dropped_vlm_na = 0
    dropped_scope = 0
    for oid, key, cls, human in human_labels:
        vlm = stored.get((oid, key))
        if vlm is None:
            unmatched += 1
            continue
        if scope_by_output is not None and not is_assessable(
            scope_by_output.get(oid), {"key": key, "trait_class": cls}
        ):
            dropped_scope += 1
            continue
        if vlm in KAPPA_EXCLUDED_VERDICTS:
            dropped_vlm_na += 1
            continue
        h, m = by_class.setdefault(cls, ([], []))
        h.append(human)
        m.append(vlm)
    return by_class, {
        "unmatched": unmatched,
        "dropped_vlm_na": dropped_vlm_na,
        "dropped_scope": dropped_scope,
    }


def accepted_trait_classes(db: Session) -> set[str]:
    return {
        c.trait_class
        for c in db.execute(
            select(TraitCalibration).where(TraitCalibration.accepted.is_(True))
        ).scalars()
    }


def recompute_trait_calibration(db: Session, human_labels, judge_model: str | None = None) -> dict:
    """human_labels: iterable of (output_id, trait_key, trait_class, human_verdict). Pairs with
    stored TraitVerdicts on (output_id, trait_key); per class, Cohen's kappa of human vs VLM.

    Verdicts are filtered to a single judge_model so a second model can't collide on the
    (output_id, trait_key) key (last-write-wins) and silently corrupt the agreement count."""
    if judge_model is None:
        from . import judge

        judge_model = judge.JUDGE_MODEL
    stored = {
        (v.output_id, v.trait_key): v.verdict
        for v in db.execute(select(TraitVerdict)).scalars()
        if v.judge_model == judge_model
    }
    scopes = load_scopes(db)
    by_class, _ = calibration_pairs_by_class(human_labels, stored, scope_by_output=scopes or None)
    written = 0
    for cls, (h, m) in by_class.items():
        k = cohens_kappa(h, m)
        n = len(h)
        accepted = k is not None and k >= MODE_C_KAPPA_BAR and n >= MODE_C_MIN_N
        row = (
            db.execute(select(TraitCalibration).where(TraitCalibration.trait_class == cls))
            .scalars()
            .first()
        )
        if row is None:
            row = TraitCalibration(trait_class=cls)
            db.add(row)
        row.kappa, row.n, row.accepted = k, n, accepted
        written += 1
    db.commit()
    return {"classes": written}


def recompute_trait_scores(db: Session, judge_model: str | None = None) -> dict:
    if judge_model is None:
        from . import judge

        judge_model = judge.JUDGE_MODEL
    accepted = accepted_trait_classes(db)
    scopes = load_scopes(db)
    by_output: dict[int, list] = {}
    for v in db.execute(select(TraitVerdict)).scalars():
        if v.judge_model != judge_model:
            continue  # one model per score; mixing would double-count verdicts
        by_output.setdefault(v.output_id, []).append(v)
    n_out = 0
    for oid, verdicts in by_output.items():
        # A verdict counts only if its class is calibrated, the VLM made a scoreable call, AND
        # the trait is assessable on what THIS model depicts — so a VLM 'habit present' on a
        # single-fruit model is dropped, not counted as botanical accuracy.
        scored = [
            v
            for v in verdicts
            if v.trait_class in accepted
            and v.verdict != "not_assessable"
            and is_assessable(scopes.get(oid), {"key": v.trait_key, "trait_class": v.trait_class})
        ]
        n_scored = len(scored)
        correct = sum(1 for v in scored if v.verdict == "present_correct")
        acc = (correct / n_scored) if n_scored else None
        row = db.execute(select(TraitScore).where(TraitScore.output_id == oid)).scalars().first()
        if row is None:
            row = TraitScore(output_id=oid)
            db.add(row)
        row.botanical_accuracy = acc
        row.n_scored = n_scored
        row.n_total = len(verdicts)
        row.judge_model = judge_model
        n_out += 1
    db.commit()
    return {"outputs": n_out}


def trait_leaderboard(db: Session) -> list[dict]:
    """Generator-level mean botanical-accuracy over scored outputs (calibrated classes only)."""
    names = generator_display_names(db)
    excluded = mode_a_excluded_generator_ids(db)
    agg: dict[int, list] = {}
    for ts in db.execute(select(TraitScore)).scalars():
        if ts.botanical_accuracy is None:
            continue
        out = db.get(ModelOutput, ts.output_id)
        if out is None or out.generator_id in excluded or out.is_gold:
            continue
        agg.setdefault(out.generator_id, []).append(ts.botanical_accuracy)
    rows = [
        {
            "generator": names.get(gid, str(gid)),
            "botanical_accuracy": round(sum(v) / len(v), 3),
            "n_outputs": len(v),
        }
        for gid, v in agg.items()
    ]
    rows.sort(key=lambda r: r["botanical_accuracy"], reverse=True)
    return rows


def tier_trait_accuracy(db: Session) -> list[dict]:
    """Per-difficulty-tier mean botanical accuracy over scored outputs (calibrated classes
    only), ordered by TIERS. Empty until a trait class passes the kappa-gate (all scores
    None) — the difficulty view hides the section until then. Feeds axis-A's phylogenetic
    difficulty map later."""
    from .difficulty import TIERS

    tier_of = {td.task_id: td.tier for td in db.execute(select(TaskDifficulty)).scalars()}
    agg: dict[str, list] = {}
    for ts in db.execute(select(TraitScore)).scalars():
        if ts.botanical_accuracy is None:
            continue
        out = db.get(ModelOutput, ts.output_id)
        if out is None or out.is_gold:
            continue
        tier = tier_of.get(out.task_id)
        if tier is None:
            continue
        agg.setdefault(tier, []).append(ts.botanical_accuracy)
    return [
        {
            "tier": t,
            "mean_accuracy": round(sum(agg[t]) / len(agg[t]), 3),
            "n_outputs": len(agg[t]),
        }
        for t in TIERS
        if agg.get(t)
    ]


def completeness_rows(db) -> list[dict]:
    """Per-output completeness rows for /api/completeness.json (taxon via the output's
    task rubric; None when no rubric)."""
    from app.models import Completeness, ModelOutput, TraitRubric

    out = []
    for c in db.query(Completeness).all():
        mo = db.get(ModelOutput, c.output_id)
        taxon = None
        if mo is not None:
            rubric = db.query(TraitRubric).filter_by(task_id=mo.task_id).first()
            taxon = rubric.taxon if rubric else None
        out.append(
            {
                "output_id": c.output_id,
                "taxon": taxon,
                "generator_id": mo.generator_id if mo else None,
                "category": c.category,
                "score": c.score,
            }
        )
    return out


def dgen_trajectory(db, run_id: int | None = None) -> list[dict]:
    """Per (run, taxon) the ordered refinement rounds + the round-0->best fidelity lift."""
    import collections

    from app.models import DGenIteration, DGenRun

    q = db.query(DGenIteration)
    if run_id is not None:
        q = q.filter_by(run_id=run_id)
    model_by_run = {r.id: r.model_id for r in db.query(DGenRun).all()}

    groups: dict[tuple[int, str], list] = collections.defaultdict(list)
    for it in q.all():
        groups[(it.run_id, it.taxon)].append(it)

    out = []
    for (rid, taxon), iters in groups.items():
        iters.sort(key=lambda i: i.round)
        rounds = [
            {
                "round": i.round,
                "fidelity": i.fidelity,
                "completeness_category": i.completeness_category,
                "status": i.status,
                "is_best": i.is_best,
            }
            for i in iters
        ]
        fid0 = iters[0].fidelity if iters else None
        best = next((i.fidelity for i in iters if i.is_best), None)
        if best is None:
            valids = [i.fidelity for i in iters if i.fidelity is not None]
            best = max(valids) if valids else None
        lift = (best - fid0) if (best is not None and fid0 is not None) else None
        out.append(
            {
                "run_id": rid,
                "model_id": model_by_run.get(rid, ""),
                "taxon": taxon,
                "rounds": rounds,
                "fidelity_0": fid0,
                "fidelity_best": best,
                "lift": lift,
            }
        )
    return out


def _gallery_slug(title: str) -> str:
    """'Lycoperdon perlatum — single-image → …' -> 'lycoperdon_perlatum' (gallery dir name)."""
    return title.split("—")[0].strip().lower().replace(" ", "_")


@functools.lru_cache(maxsize=128)
def _gallery_manifest(slug: str) -> tuple[dict, ...]:
    """The reference gallery manifest for `slug`, read through the STORAGE BACKEND.

    Reading it off the local filesystem is what made the galleries vanish in production: the
    image excludes `data/`, so `config.ASSET_DIR` is empty on the public instance and every
    `Path.exists()` was False while the photos sat in R2. The image URLs beside this always went
    through `storage.url_for()`; only the manifest test did not, so the whole gallery silently
    disappeared while working perfectly in dev.

    Cached because it is static for the lifetime of a deploy (the gallery only changes via a new
    bundle import or image), and this sits on the /api/next hot path where an S3 round trip per
    request would be pure latency. Tests mutating the gallery must call
    `reference_gallery_cache_clear()`.

    A missing gallery is normal — not every taxon has one — so a miss returns () rather than
    raising.
    """
    st = get_storage()
    rel = f"reference/gallery/{slug}/manifest.json"
    try:
        if not st.exists(rel):
            return ()
        items = json.loads(st.read(rel))
    except (ValueError, TypeError, OSError):
        return ()
    return tuple(i for i in items if isinstance(i, dict))


def reference_gallery_cache_clear() -> None:
    """Drop the cached gallery manifests (tests, and any in-process gallery swap)."""
    _gallery_manifest.cache_clear()


def reference_images_for_task(db: Session, task) -> list[dict]:
    """Ordered reference images for a task, each {url, credit}: an independent CC species gallery
    (data/assets/reference/gallery/<slug>/, sourced from iNaturalist) so voters judge fidelity
    against the organism, not against a recon's own input photo. The recon INPUT photo is NOT a
    reference (showing it is circular/biased — a recon that reproduces its input reads as faithful
    even if biologically wrong); it is suppressed EXCEPT for tasks whose gallery-slug is in
    config.INPUT_REFERENCE_EXEMPT_SLUGS (barley-MRI: a root stand-in with no whole-plant gallery).
    Only QA-passed gallery images are shown. Task-scoped; empty list if nothing is on record.
    cc-by gallery photos carry their required attribution in `credit`."""
    from .models import ModelOutput
    from .reference_provenance import _image_name, cleared_reference_images

    st = get_storage()
    out: list[dict] = []
    seen: set[str] = set()
    slug = _gallery_slug(task.title)

    # Recon input photos are shown ONLY for exempt tasks (barley-MRI). For everyone else the input
    # is not a reference — the independent gallery below is the anchor. Text→3D never contributed
    # an image input, so this is paradigm-agnostic. Visible-only + cleared-photo still apply
    # (clearance is per PHOTO, not per taxon — a cleared photo does not clear its taxon-mates).
    if slug in config.INPUT_REFERENCE_EXEMPT_SLUGS:
        cleared_images = cleared_reference_images()
        for o in db.execute(
            select(ModelOutput).where(
                ModelOutput.task_id == task.id, ModelOutput.hidden_at.is_(None)
            )
        ).scalars():
            try:
                img = (json.loads(o.meta_json or "{}") or {}).get("input_image")
            except (ValueError, TypeError):
                continue
            if img and img not in seen and _image_name(img) in cleared_images:
                seen.add(img)
                out.append({"url": st.url_for(img), "credit": "reconstruction input photo"})

    for item in _gallery_manifest(slug):
        # QA-failed reference images (fruit-only / isolated / species mismatch) are not
        # shown. Default-true so un-scored legacy manifests are unaffected until scored.
        if not item.get("passed_qa", True):
            continue
        if "file" not in item:
            continue
        rel = f"reference/gallery/{slug}/{item['file']}"
        out.append({"url": st.url_for(rel), "credit": item.get("attribution", "iNaturalist")})
    return out
