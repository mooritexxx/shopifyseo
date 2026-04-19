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


class AdaptiveMinuteRateLimiter:
    """Sliding-window rate limiter with simple adaptive slow-down / recovery."""

    def __init__(
        self,
        initial_limit: int,
        *,
        minimum_limit: int,
        maximum_limit: int,
        period_seconds: int = 60,
        on_granted: Callable[[float], None] | None = None,
        recovery_step: int = 5,
        recovery_successes: int = 25,
    ) -> None:
        self.period_seconds = max(int(period_seconds or 0), 1)
        self.minimum_limit = max(int(minimum_limit or 0), 1)
        self.maximum_limit = max(int(maximum_limit or 0), self.minimum_limit)
        self._limit = max(min(int(initial_limit or 0), self.maximum_limit), self.minimum_limit)
        self._on_granted = on_granted
        self._recovery_step = max(int(recovery_step or 0), 1)
        self._recovery_successes = max(int(recovery_successes or 0), 1)
        self._lock = threading.Lock()
        self._request_times: deque[float] = deque()
        self._cooldown_until = 0.0
        self._success_streak = 0

    @property
    def current_limit(self) -> int:
        with self._lock:
            return self._limit

    @property
    def max_inflight(self) -> int:
        """Fixed concurrency cap as requested."""
        return 25

    def _trim_unlocked(self, now: float) -> None:
        cutoff = now - self.period_seconds
        while self._request_times and self._request_times[0] <= cutoff:
            self._request_times.popleft()

    def _wait_seconds_unlocked(self, now: float) -> float:
        self._trim_unlocked(now)
        wait_seconds = 0.0
        if now < self._cooldown_until:
            wait_seconds = max(self._cooldown_until - now, 0.05)
        limit = max(int(self._limit), 1)
        if len(self._request_times) >= limit:
            wait_seconds = max(wait_seconds, max(self.period_seconds - (now - self._request_times[0]), 0.05))
        if self._request_times:
            min_interval_seconds = self.period_seconds / float(limit)
            since_last = now - self._request_times[-1]
            if since_last < min_interval_seconds:
                wait_seconds = max(wait_seconds, max(min_interval_seconds - since_last, 0.05))
        return wait_seconds

    def wait_seconds(self) -> float:
        with self._lock:
            return self._wait_seconds_unlocked(time.monotonic())

    def acquire(self, cancel_check: Callable[[], None] | None = None) -> None:
        while True:
            if cancel_check is not None:
                cancel_check()
            with self._lock:
                now = time.monotonic()
                wait_seconds = self._wait_seconds_unlocked(now)
                limit = max(int(self._limit), 1)
                if wait_seconds <= 0.0:
                    self._request_times.append(now)
                    if self._on_granted is not None:
                        self._on_granted(now)
                    return
            time.sleep(wait_seconds)

    def note_success(self) -> tuple[bool, int]:
        with self._lock:
            self._success_streak += 1
            if self._limit >= self.maximum_limit or self._success_streak < self._recovery_successes:
                return False, self._limit
            self._success_streak = 0
            self._limit = min(self.maximum_limit, self._limit + self._recovery_step)
            return True, self._limit

    def note_rate_limited(self, retry_after_seconds: float | None = None) -> tuple[bool, int]:
        with self._lock:
            now = time.monotonic()
            self._success_streak = 0
            reduced = max(self.minimum_limit, int(self._limit * 0.75))
            changed = reduced != self._limit
            self._limit = reduced
            cooldown_seconds = retry_after_seconds if retry_after_seconds is not None else 0.0
            cooldown_seconds = min(max(float(cooldown_seconds), 5.0), 30.0)
            self._cooldown_until = max(self._cooldown_until, now + cooldown_seconds)
            return changed, self._limit
