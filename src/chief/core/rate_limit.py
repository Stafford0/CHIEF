from __future__ import annotations

import time
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass
from math import ceil
from threading import Lock


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    remaining: int
    retry_after_seconds: int = 0


class SlidingWindowRateLimiter:
    """Bounded in-process limiter for the small, single-node CHIEF service.

    This protects authenticated LAN deployments from accidental floods and
    basic brute force attempts. A multi-node deployment should replace it with
    a shared limiter at the TLS gateway without changing the API middleware.
    """

    def __init__(
        self,
        limit: int,
        *,
        window_seconds: float = 60.0,
        max_keys: int = 10_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if limit < 1:
            raise ValueError("Rate limit must be positive.")
        if window_seconds <= 0:
            raise ValueError("Rate-limit window must be positive.")
        if max_keys < 1:
            raise ValueError("Rate-limit key budget must be positive.")
        self.limit = limit
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self._clock = clock
        self._requests: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = Lock()

    def check(self, key: str) -> RateLimitDecision:
        if not key:
            raise ValueError("Rate-limit key cannot be empty.")
        now = self._clock()
        cutoff = now - self.window_seconds
        with self._lock:
            timestamps = self._requests.pop(key, deque())
            while timestamps and timestamps[0] <= cutoff:
                timestamps.popleft()

            if len(timestamps) >= self.limit:
                retry_after = max(1, ceil(timestamps[0] + self.window_seconds - now))
                self._requests[key] = timestamps
                return RateLimitDecision(False, 0, retry_after)

            timestamps.append(now)
            self._requests[key] = timestamps
            while len(self._requests) > self.max_keys:
                self._requests.popitem(last=False)
            return RateLimitDecision(True, self.limit - len(timestamps))
