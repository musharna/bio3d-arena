"""Vote-integrity / anti-abuse primitives for the public arena.

- Sliding-window rate limiting (in-process; production should back this with Redis).
- Per-session dedup so one voter can't farm the same pairing repeatedly.
- Gold attention-check trust scoring (Laplace-smoothed pass rate).
- Pluggable captcha verification (off unless BIO3D_REQUIRE_CAPTCHA).
"""

from __future__ import annotations

import functools
import time
from collections import defaultdict, deque

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config
from .models import Comparison, VoterSession, Vote


class InMemoryRateLimiter:
    """Per-process sliding-window limiter. Fine for a single worker / dev."""

    def __init__(self) -> None:
        self._buckets: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, session_id: str) -> bool:
        now = time.monotonic()
        dq = self._buckets[session_id]
        cutoff = now - config.VOTE_RATE_WINDOW
        while dq and dq[0] < cutoff:
            dq.popleft()
        if len(dq) >= config.VOTE_RATE_LIMIT:
            return False
        dq.append(now)
        return True

    def reset(self) -> None:
        self._buckets.clear()


class RedisRateLimiter:
    """Distributed fixed-window limiter shared across workers (redis is lazy)."""

    def __init__(self, redis_url: str) -> None:
        import redis  # lazy: only when BIO3D_REDIS_URL is configured

        self._redis = redis.Redis.from_url(redis_url)

    def allow(self, session_id: str) -> bool:
        window = int(config.VOTE_RATE_WINDOW)
        # Bucket key per window slot → fixed-window counter with auto-expiry.
        slot = int(time.time()) // window
        key = f"bio3d:rl:{session_id}:{slot}"
        count = self._redis.incr(key)
        if count == 1:
            self._redis.expire(key, window)
        return count <= config.VOTE_RATE_LIMIT

    def reset(self) -> None:  # best-effort; used by tests (not against real redis)
        pass


@functools.lru_cache(maxsize=1)
def _limiter():
    return RedisRateLimiter(config.REDIS_URL) if config.REDIS_URL else InMemoryRateLimiter()


def check_rate_limit(session_id: str) -> bool:
    """Returns True if the vote is allowed, False if over the limit."""
    return _limiter().allow(session_id)


def reset_rate_limits() -> None:
    """Clear rate state (used by tests)."""
    _limiter().reset()


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
