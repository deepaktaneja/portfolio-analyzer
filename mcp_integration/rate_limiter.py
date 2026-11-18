"""Token-bucket rate limiting for MCP/API calls (per-server).

Prevents excessive upstream calls. Thread-safe with a simple lock; time source
injectable for deterministic tests.
"""
from __future__ import annotations

import threading
import time


class TokenBucket:
    """Classic token bucket: ``rate`` tokens/second, up to ``capacity`` burst."""

    def __init__(self, rate: float = 1.0, capacity: int = 1, clock=time.monotonic):
        if rate <= 0:
            raise ValueError("rate must be > 0")
        self.rate = float(rate)
        self.capacity = max(1, int(capacity))
        self._tokens = float(self.capacity)
        self._clock = clock
        self._last = clock()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        now = self._clock()
        elapsed = now - self._last
        if elapsed > 0:
            self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
            self._last = now

    def try_acquire(self, n: int = 1) -> bool:
        with self._lock:
            self._refill()
            if self._tokens >= n:
                self._tokens -= n
                return True
            return False

    def wait_seconds(self, n: int = 1) -> float:
        with self._lock:
            self._refill()
            if self._tokens >= n:
                return 0.0
            return (n - self._tokens) / self.rate

    def acquire(self, n: int = 1, timeout: float | None = None) -> bool:
        """Block until tokens available or timeout. Returns success."""
        deadline = None if timeout is None else self._clock() + timeout
        while True:
            if self.try_acquire(n):
                return True
            wait = self.wait_seconds(n)
            if deadline is not None and self._clock() + wait > deadline:
                return False
            time.sleep(min(wait, 0.25))


class RateLimiterRegistry:
    """Lazily-created per-server token buckets."""

    def __init__(self, default_rate: float = 1.0, default_capacity: int = 5):
        self.default_rate = default_rate
        self.default_capacity = default_capacity
        self._buckets: dict[str, TokenBucket] = {}
        self._lock = threading.Lock()

    def get(self, server: str, rate: float | None = None, capacity: int | None = None) -> TokenBucket:
        key = str(server)
        with self._lock:
            if key not in self._buckets:
                self._buckets[key] = TokenBucket(rate or self.default_rate, capacity or self.default_capacity)
            return self._buckets[key]

    def try_acquire(self, server: str, n: int = 1) -> bool:
        return self.get(server).try_acquire(n)
