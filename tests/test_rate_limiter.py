import asyncio
import logging

import pytest

from apartmentfinder.infrastructure.config import SourceLimitSettings
from apartmentfinder.infrastructure.rate_limiter import (
    BrowserFallbackRateLimitError,
    SourceRateLimiter,
)


def test_rate_limiter_applies_min_delay_and_logs_throttling(caplog) -> None:
    now = 0.0
    sleeps: list[float] = []

    def clock() -> float:
        return now

    async def sleep(seconds: float) -> None:
        nonlocal now
        sleeps.append(seconds)
        now += seconds

    limiter = SourceRateLimiter(clock=clock, sleeper=sleep)
    limits = SourceLimitSettings(
        max_requests_per_minute=60,
        min_delay=2,
        max_delay=10,
        jitter=0,
    )

    async def run() -> None:
        await limiter.acquire_request("kufar", limits)
        await limiter.acquire_request("kufar", limits)

    with caplog.at_level(logging.INFO, logger="apartmentfinder.infrastructure"):
        asyncio.run(run())

    assert sleeps == [2]
    assert "source_request_throttled source=kufar delay_seconds=2.000" in caplog.text


def test_rate_limiter_uses_rolling_request_window() -> None:
    now = 0.0
    sleeps: list[float] = []

    def clock() -> float:
        return now

    async def sleep(seconds: float) -> None:
        nonlocal now
        sleeps.append(seconds)
        now += seconds

    limiter = SourceRateLimiter(clock=clock, sleeper=sleep)
    limits = SourceLimitSettings(
        max_requests_per_minute=1,
        min_delay=0,
        max_delay=60,
        jitter=0,
    )

    async def run() -> None:
        await limiter.acquire_request("realt", limits)
        await limiter.acquire_request("realt", limits)

    asyncio.run(run())

    assert sleeps == [60]


def test_rate_limiter_enters_cooldown_after_error_series(caplog) -> None:
    now = 10.0
    sleeps: list[float] = []

    def clock() -> float:
        return now

    async def sleep(seconds: float) -> None:
        nonlocal now
        sleeps.append(seconds)
        now += seconds

    limiter = SourceRateLimiter(clock=clock, sleeper=sleep)
    limits = SourceLimitSettings(
        max_requests_per_minute=60,
        min_delay=0,
        max_delay=7,
        jitter=0,
        cooldown_after_errors=2,
    )

    with caplog.at_level(logging.WARNING, logger="apartmentfinder.infrastructure"):
        limiter.record_error("kufar", limits)
        limiter.record_error("kufar", limits)
        asyncio.run(limiter.acquire_request("kufar", limits))

    assert sleeps == [7]
    assert "source_cooldown_started source=kufar cooldown_seconds=7.000" in caplog.text


def test_rate_limiter_resets_error_series_on_success() -> None:
    limiter = SourceRateLimiter()
    limits = SourceLimitSettings(cooldown_after_errors=2)

    limiter.record_error("kufar", limits)
    limiter.record_success("kufar")
    limiter.record_error("kufar", limits)

    assert limiter._states["kufar"].cooldown_until is None


def test_browser_fallback_limit_is_separate_and_logs_skip(caplog) -> None:
    limiter = SourceRateLimiter(clock=lambda: 0.0)
    limits = SourceLimitSettings(browser_fallback_limit=1)

    async def run() -> None:
        await limiter.acquire_browser_fallback("realt", limits)
        with pytest.raises(BrowserFallbackRateLimitError):
            await limiter.acquire_browser_fallback("realt", limits)

    with caplog.at_level(logging.WARNING, logger="apartmentfinder.infrastructure"):
        asyncio.run(run())

    assert "source_browser_fallback_skipped source=realt limit=1" in caplog.text
