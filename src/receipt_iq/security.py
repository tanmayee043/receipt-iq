"""Login sessions and rate limits, kept in Redis (or, for the hosted demo, in memory).

A session is a random token. Only its hash is stored, so reading Redis doesn't give anyone
a usable cookie, and deleting the key logs out for real, which a signed cookie can't do.

Rate limits count requests per IP in fixed one-minute windows. A burst across a window's
edge can reach twice the limit, which is fine for protecting a free quota.
"""

import hashlib
import secrets
import threading
import time
from collections import Counter

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from redis import Redis

WINDOW_SECONDS = 60


def hash_password(password: str) -> str:
    return PasswordHasher().hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return PasswordHasher().verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def _session_key(token: str) -> str:
    return f"session:{hashlib.sha256(token.encode()).hexdigest()}"


def create_session(redis: Redis, hours: int) -> str:
    token = secrets.token_urlsafe(32)
    redis.set(_session_key(token), "owner", ex=hours * 3600)
    return token


def session_is_valid(redis: Redis, token: str | None) -> bool:
    return token is not None and bool(redis.exists(_session_key(token)))


def delete_session(redis: Redis, token: str | None) -> None:
    if token is not None:
        redis.delete(_session_key(token))


def seconds_until_allowed(redis: Redis, bucket: str, client: str, limit: int) -> int | None:
    """Count this request. Returns how long to wait if it's over the limit, else None."""
    now = time.time()
    window = int(now // WINDOW_SECONDS)
    key = f"rate:{bucket}:{client}:{window}"
    # Together, so a crash between the two can't leave a counter that never expires.
    pipeline = redis.pipeline()
    pipeline.incr(key)
    pipeline.expire(key, WINDOW_SECONDS * 2)
    count = pipeline.execute()[0]
    if count <= limit:
        return None
    return int(WINDOW_SECONDS - now % WINDOW_SECONDS) + 1


class LocalRateLimit:
    """The same fixed windows, counted in this process, for the hosted demo with no Redis.

    Only the current window is kept, so memory is bounded by one minute's visitors, and a
    restart resets the counts, which is fine for a demo that spends no quota.
    """

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.window = -1
        self.counts: Counter[str] = Counter()
        self.lock = threading.Lock()  # requests are handled on several threads

    def seconds_until_allowed(self, client: str) -> int | None:
        """Count this request. Returns how long to wait if it's over the limit, else None."""
        now = time.time()
        window = int(now // WINDOW_SECONDS)
        with self.lock:
            if window != self.window:
                self.window, self.counts = window, Counter()
            self.counts[client] += 1
            count = self.counts[client]
        if count <= self.limit:
            return None
        return int(WINDOW_SECONDS - now % WINDOW_SECONDS) + 1
