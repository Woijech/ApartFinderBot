"""Realt.by source adapter implementation."""

from __future__ import annotations

from apartmentfinder.domain.models import Listing, SearchRequest
from apartmentfinder.infrastructure.config import settings
from apartmentfinder.infrastructure.sources.realt.client import RealtClient


class RealtSource:
    """Listing source adapter for Realt.by."""

    code = "realt"

    def __init__(self) -> None:
        self._client = RealtClient(
            timeout_seconds=settings.bot_fetch_timeout_seconds,
            retries=settings.bot_fetch_retries,
            retry_delay_seconds=settings.bot_fetch_retry_delay_seconds,
        )

    async def search_pages(
        self,
        request: SearchRequest,
        max_pages: int,
        delay_seconds: float,
    ) -> list[Listing]:
        """Return listings from Realt.by."""
        return await self._client.search_pages(request, max_pages, delay_seconds)

    async def fetch_listing_detail(self, listing: Listing) -> Listing:
        """Fetch full Realt listing details."""
        return await self._client.fetch_listing_detail(listing)

    async def close(self) -> None:
        """Close HTTP connections."""
        await self._client.close()
