"""Vote-integrity / anti-abuse primitives for the public arena.

- Sliding-window rate limiting (in-process; production should back this with Redis).
- Per-session dedup so one voter can't farm the same pairing repeatedly.
- Gold attention-check trust scoring (Laplace-smoothed pass rate).
- Pluggable captcha verification (off unless BIO3D_REQUIRE_CAPTCHA).
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config
from .models import Comparison, VoterSession, Vote

# session_id -> deque[monotonic timestamps] within the current window.
_buckets: dict[str, deque[float]] = defaultdict(deque)


def check_rate_limit(session_id: str) -> bool:
    """Sliding-window limiter. Returns True if the vote is allowed, False if over."""
    now = time.monotonic()
    dq = _buckets[session_id]
    cutoff = now - config.VOTE_RATE_WINDOW
    while dq and dq[0] < cutoff:
        dq.popleft()
    if len(dq) >= config.VOTE_RATE_LIMIT:
        return False
    dq.append(now)
    return True


def reset_rate_limits() -> None:
    """Clear in-memory rate state (used by tests)."""
    _buckets.clear()


def verify_captcha(token: str | None) -> bool:
    """Verify a human-check token. No-op unless REQUIRE_CAPTCHA is enabled.

    Integration point: when enabling, validate `token` against Turnstile/hCaptcha
    here. The stub accepts any non-empty token so the seam is wired + testable.
    """
    if not config.REQUIRE_CAPTCHA:
        return True
    return bool(token)


def get_or_create_session(db: Session, session_id: str) -> VoterSession:
    vs = db.get(VoterSession, session_id)
    if vs is None:
        vs = VoterSession(session_id=session_id)
        db.add(vs)
        db.flush()
    return vs


def note_vote(db: Session, session_id: str) -> VoterSession:
    vs = get_or_create_session(db, session_id)
    vs.n_votes += 1
    return vs


def record_gold_outcome(db: Session, session_id: str, passed: bool) -> VoterSession:
    """Update a session's trust from a gold attention-check outcome."""
    vs = get_or_create_session(db, session_id)
    vs.gold_seen += 1
    if passed:
        vs.gold_passed += 1
    # Laplace-smoothed pass rate: starts at 1.0, decays with failures.
    vs.trust = (vs.gold_passed + 1) / (vs.gold_seen + 1)
    return vs


def already_voted_pair(
    db: Session, session_id: str, output_a_id: int, output_b_id: int, criterion_id: int
) -> bool:
    """True if this session already cast a decided vote on the same (unordered) pair."""
    pair = {output_a_id, output_b_id}
    rows = (
        db.execute(
            select(Comparison)
            .join(Vote, Vote.comparison_id == Comparison.id)
            .where(
                Comparison.session_id == session_id,
                Comparison.criterion_id == criterion_id,
                Comparison.is_gold.is_(False),
            )
        )
        .scalars()
        .all()
    )
    return any({c.output_a_id, c.output_b_id} == pair for c in rows)
