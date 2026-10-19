"""Failed sign-in throttle (in memory, per process).

After ``MAX_FAILURES`` failed attempts within ``WINDOW`` for the same email or
the same client IP, sign-in is blocked until the oldest failure leaves the
window, even with the right password, which slows down password guessing.
Counters live in memory, so a restart clears them.
"""

from __future__ import annotations

from collections import defaultdict, deque
from threading import Lock
from time import monotonic

MAX_FAILURES = 5
WINDOW_SECONDS = 15 * 60


class LoginThrottle:
    def __init__(self, max_failures: int = MAX_FAILURES, window: float = WINDOW_SECONDS):
        self.max_failures = max_failures
        self.window = window
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def _recent(self, key: str, now: float) -> deque[float]:
        failures = self._failures[key]
        while failures and now - failures[0] >= self.window:
            failures.popleft()
        return failures

    def retry_after(self, *keys: str) -> int:
        """Seconds until a blocked key may try again (0 when not blocked)."""
        now = monotonic()
        with self._lock:
            waits = [
                self.window - (now - failures[0])
                for failures in (self._recent(key, now) for key in keys)
                if len(failures) >= self.max_failures
            ]
        return int(max(waits)) + 1 if waits else 0

    def record_failure(self, *keys: str) -> None:
        now = monotonic()
        with self._lock:
            for key in keys:
                self._recent(key, now).append(now)

    def clear(self) -> None:
        with self._lock:
            self._failures.clear()

    def reset(self, *keys: str) -> None:
        with self._lock:
            for key in keys:
                self._failures.pop(key, None)


login_throttle = LoginThrottle()
