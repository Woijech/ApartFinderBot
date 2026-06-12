"""Per-source async rate limiting and cooldown control."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from apartmentfinder.infrastructure.config import SourceLimitSettings

logger = logging.getLogger(__name__)

Clock = Callable[[], float]
Sleeper = Callable[[float], Awaitable[None]]


class BrowserFallbackRateLimitError(RuntimeError):
    """Raised when browser fallback is skipped by a per-source limit."""


@dataclass
class SourceLimitState:
    """Mutable limiter state for one source."""

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    request_times: deque[float] = field(default_factory=deque)
    browser_fallback_times: deque[float] = field(default_factory=deque)
    last_request_at: float | None = None
    consecutive_errors: int = 0
    cooldown_until: float | None = None


class SourceRateLimiter:
    """Coordinate per-source request rate, jitter, cooldown, and browser budget."""

    def __init__(
        self,
        *,
        clock: Clock = time.monotonic,
        sleeper: Sleeper = asyncio.sleep,
    ) -> None:
        self._clock = clock
        self._sleep = sleeper
        self._states: dict[str, SourceLimitState] = defaultdict(SourceLimitState)

    async def acquire_request(
        self,
        source: str,
        limits: SourceLimitSettings,
    ) -> None:
        """Wait until a normal source request is allowed."""
        source = source.casefold()
        state = self._states[source]
        async with state.lock:
            delay = self._request_delay(source, state, limits)
            if delay > 0:
                logger.info(
                    "source_request_throttled source=%s delay_seconds=%.3f",
                    source,
                    delay,
                )
                await self._sleep(delay)
            now = self._clock()
            prune_old(state.request_times, now)
            state.request_times.append(now)
            state.last_request_at = now

    async def acquire_browser_fallback(
        self,
        source: str,
        limits: SourceLimitSettings,
    ) -> None:
        """Reserve one browser fallback attempt or raise when over budget."""
        source = source.casefold()
        state = self._states[source]
        async with state.lock:
            now = self._clock()
            prune_old(state.browser_fallback_times, now)
            if len(state.browser_fallback_times) >= limits.browser_fallback_limit:
                logger.warning(
                    "source_browser_fallback_skipped source=%s limit=%s",
                    source,
                    limits.browser_fallback_limit,
                )
                raise BrowserFallbackRateLimitError(
                    f"Browser fallback limit reached for {source}"
                )
            state.browser_fallback_times.append(now)

    def record_success(self, source: str) -> None:
        """Reset consecutive error state after a successful source request."""
        state = self._states[source.casefold()]
        state.consecutive_errors = 0

    def record_error(self, source: str, limits: SourceLimitSettings) -> None:
        """Record one final source error and maybe enter cooldown."""
        source = source.casefold()
        state = self._states[source]
        state.consecutive_errors += 1
        if state.consecutive_errors < limits.cooldown_after_errors:
            return
        cooldown_seconds = limits.max_delay
        state.cooldown_until = self._clock() + cooldown_seconds
        state.consecutive_errors = 0
        logger.warning(
            "source_cooldown_started source=%s cooldown_seconds=%.3f",
            source,
            cooldown_seconds,
        )

    def _request_delay(
        self,
        source: str,
        state: SourceLimitState,
        limits: SourceLimitSettings,
    ) -> float:
        now = self._clock()
        delay = 0.0
        if state.cooldown_until is not None and state.cooldown_until > now:
            delay = max(delay, state.cooldown_until - now)
        prune_old(state.request_times, now)
        if len(state.request_times) >= limits.max_requests_per_minute:
            delay = max(delay, 60 - (now - state.request_times[0]))
        if state.last_request_at is not None:
            delay = max(delay, limits.min_delay - (now - state.last_request_at))
        if delay > 0 and limits.jitter > 0:
            delay += random.uniform(0, limits.jitter)
        if limits.max_delay > 0:
            delay = min(delay, limits.max_delay)
        return max(delay, 0.0)


def prune_old(times: deque[float], now: float) -> None:
    """Keep only timestamps inside the rolling one-minute window."""
    while times and now - times[0] >= 60:
        times.popleft()


source_rate_limiter = SourceRateLimiter()
