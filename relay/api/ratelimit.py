"""A small in-process rate limiter for sign-in (PRD 18). One API process is enough for the demo."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Callable
from threading import Lock


class SlidingWindowLimiter:
    """Allows at most `limit` calls per key in any `window_seconds`."""

    def __init__(
        self, limit: int, window_seconds: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        """Configure the limit. `clock` is injectable for tests."""
        self._limit = limit
        self._window = window_seconds
        self._clock = clock
        self._calls: defaultdict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def allow(self, key: str) -> bool:
        """Record a call and return whether it is within the limit."""
        now = self._clock()
        with self._lock:
            calls = self._calls[key]
            while calls and calls[0] <= now - self._window:
                calls.popleft()
            if len(calls) >= self._limit:
                return False
            calls.append(now)
            return True

    def reset(self) -> None:
        """Forget all calls."""
        with self._lock:
            self._calls.clear()


login_limiter = SlidingWindowLimiter(limit=30, window_seconds=60)
