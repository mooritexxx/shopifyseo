"""Sliding-window per-minute rate limiter (used by sync workers and shared gates)."""

from collections import deque
import threading
import time
from collections.abc import Callable


class PerMinuteRateLimiter:
    def __init__(
        self,
        limit: int,
        period_seconds: int = 60,
        *,
        on_granted: Callable[[float], None] | None = None,
    ) -> None:
        self.limit = max(int(limit or 0), 1)
        self.period_seconds = max(int(period_seconds or 0), 1)
        self._on_granted = on_granted
        self._lock = threading.Lock()
        self._request_times: deque[float] = deque()

    def acquire(self, cancel_check: Callable[[], None] | None = None) -> None:
        while True:
            if cancel_check is not None:
                cancel_check()
            with self._lock:
                now = time.monotonic()
                cutoff = now - self.period_seconds
                while self._request_times and self._request_times[0] <= cutoff:
                    self._request_times.popleft()
                if len(self._request_times) < self.limit:
                    self._request_times.append(now)
                    if self._on_granted is not None:
                        self._on_granted(now)
                    return
                wait_seconds = max(self.period_seconds - (now - self._request_times[0]), 0.05)
            time.sleep(wait_seconds)
