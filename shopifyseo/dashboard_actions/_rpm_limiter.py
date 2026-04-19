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
        min_interval_seconds: float = 0.0,
    ) -> None:
        self.limit = max(int(limit or 0), 1)
        self.period_seconds = max(int(period_seconds or 0), 1)
        self._on_granted = on_granted
        self.min_interval_seconds = max(float(min_interval_seconds or 0.0), 0.0)
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
                wait_seconds = 0.0
                if len(self._request_times) >= self.limit:
                    wait_seconds = max(self.period_seconds - (now - self._request_times[0]), 0.05)
                if self.min_interval_seconds > 0 and self._request_times:
                    since_last = now - self._request_times[-1]
                    if since_last < self.min_interval_seconds:
                        wait_seconds = max(wait_seconds, max(self.min_interval_seconds - since_last, 0.05))
                if wait_seconds <= 0.0:
                    self._request_times.append(now)
                    if self._on_granted is not None:
                        self._on_granted(now)
                    return
            time.sleep(wait_seconds)
