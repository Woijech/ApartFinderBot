import asyncio
import logging

import pytest

from apartmentfinder.application.source_registry import (
    SourceNetworkError,
    fetch_from_sources,
)
from apartmentfinder.domain.models import Listing, SearchRequest
from apartmentfinder.infrastructure.source_registry import configured_sources


class FakeSource:
    """Small source double used to test aggregation without network."""

    def __init__(self, code, listings=None, error=None):
        self.code = code
        self._listings = listings or []
        self._error = error

    async def search_pages(self, request, max_pages, delay_seconds):
        if self._error:
            raise self._error
        return self._listings

    async def close(self):
        pass


def test_fetch_from_sources_combines_multiple_sources() -> None:
    sources = [
        FakeSource(
            "kufar",
            [Listing(ad_id=1, title="Kufar", url="https://k.test/1")],
        ),
        FakeSource(
            "realt",
            [
                Listing(
                    ad_id=1,
                    title="Realt",
                    url="https://r.test/1",
                    source="realt",
                )
            ],
        ),
    ]

    listings = asyncio.run(
        fetch_from_sources(
            SearchRequest(),
            sources,
            max_pages=1,
            delay_seconds=0,
            concurrency=2,
        )
    )

    assert [(listing.source, listing.ad_id) for listing in listings] == [
        ("kufar", 1),
        ("realt", 1),
    ]


def test_fetch_from_sources_logs_successful_source(caplog) -> None:
    sources = [
        FakeSource(
            "realt",
            [Listing(ad_id=2, title="Realt", url="https://r.test/2", source="realt")],
        ),
    ]

    with caplog.at_level(logging.INFO, logger="apartmentfinder.application"):
        asyncio.run(
            fetch_from_sources(
                SearchRequest(),
                sources,
                max_pages=1,
                delay_seconds=0,
                concurrency=2,
            )
        )

    assert "listing_source_check_finished source=realt count=1" in caplog.text
    assert "listing_sources_finished total=1 sources={'realt': 1}" in caplog.text


def test_fetch_from_sources_keeps_working_when_one_source_fails() -> None:
    sources = [
        FakeSource("kufar", error=RuntimeError("blocked")),
        FakeSource(
            "realt",
            [Listing(ad_id=2, title="Realt", url="https://r.test/2", source="realt")],
        ),
    ]

    listings = asyncio.run(
        fetch_from_sources(
            SearchRequest(),
            sources,
            max_pages=1,
            delay_seconds=0,
            concurrency=2,
        )
    )

    assert [listing.source for listing in listings] == ["realt"]


def test_fetch_from_sources_logs_failed_source(caplog) -> None:
    sources = [
        FakeSource("kufar", error=RuntimeError("blocked")),
        FakeSource(
            "realt",
            [Listing(ad_id=2, title="Realt", url="https://r.test/2", source="realt")],
        ),
    ]

    with caplog.at_level(logging.WARNING, logger="apartmentfinder.application"):
        asyncio.run(
            fetch_from_sources(
                SearchRequest(),
                sources,
                max_pages=1,
                delay_seconds=0,
                concurrency=2,
            )
        )

    assert "listing_source_failed source=kufar error_type=RuntimeError" in caplog.text


def test_fetch_from_sources_raises_when_all_sources_fail() -> None:
    sources = [
        FakeSource("kufar", error=RuntimeError("blocked")),
        FakeSource("realt", error=RuntimeError("blocked")),
    ]

    with pytest.raises(SourceNetworkError):
        asyncio.run(
            fetch_from_sources(
                SearchRequest(),
                sources,
                max_pages=1,
                delay_seconds=0,
                concurrency=2,
            )
        )


def test_fetch_from_sources_limits_concurrency() -> None:
    active = 0
    max_active = 0

    class SlowSource(FakeSource):
        async def search_pages(self, request, max_pages, delay_seconds):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0)
            active -= 1
            return self._listings

    sources = [
        SlowSource("one", [Listing(ad_id=1, title="One", url="https://one.test")]),
        SlowSource("two", [Listing(ad_id=2, title="Two", url="https://two.test")]),
        SlowSource("three", [Listing(ad_id=3, title="Three", url="https://3.test")]),
    ]

    listings = asyncio.run(
        fetch_from_sources(
            SearchRequest(),
            sources,
            max_pages=1,
            delay_seconds=0,
            concurrency=1,
        )
    )

    assert [listing.ad_id for listing in listings] == [1, 2, 3]
    assert max_active == 1


def test_configured_sources_returns_kufar_and_realt_sources() -> None:
    sources = configured_sources()

    try:
        assert [source.code for source in sources] == ["kufar", "realt"]
    finally:
        for source in sources:
            asyncio.run(source.close())
