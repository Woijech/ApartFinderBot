"""HTTP client for Realt.by rental listings."""

from __future__ import annotations

import asyncio
import logging
from time import perf_counter

import httpx

from apartmentfinder.domain.models import Listing, SearchRequest
from apartmentfinder.infrastructure import browser_fetcher
from apartmentfinder.infrastructure.browser_fetcher import BrowserFetchError
from apartmentfinder.infrastructure.config import settings
from apartmentfinder.infrastructure.sources.realt.parser import (
    parse_realt_detail_page,
    parse_realt_search_page,
)

REALT_PATHS = {
    "apartment": "/rent/flat-for-long/",
    "room": "/rent/room-for-long/",
}
logger = logging.getLogger(__name__)


class RealtNetworkError(RuntimeError):
    """Raised when Realt cannot be reached after retry attempts."""


class RealtClient:
    """Asynchronous Realt.by client used by background bot jobs."""

    def __init__(
        self,
        base_url: str = settings.realt_base_url,
        timeout_seconds: float = settings.timeout_seconds,
        retries: int = settings.request_retries,
        retry_delay_seconds: float = settings.request_retry_delay_seconds,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._retries = max(retries, 0)
        self._retry_delay_seconds = max(retry_delay_seconds, 0)
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                timeout_seconds,
                connect=timeout_seconds,
                read=timeout_seconds,
            ),
            follow_redirects=True,
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "ru,en;q=0.8",
                "User-Agent": settings.user_agent,
            },
        )

    async def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        await self._client.aclose()

    async def __aenter__(self) -> RealtClient:
        """Enter an async context-managed client session."""
        return self

    async def __aexit__(self, *_args: object) -> None:
        """Close the client session when leaving a context manager."""
        await self.close()

    async def search_pages(
        self,
        request: SearchRequest,
        max_pages: int = 1,
        delay_seconds: float = 1.0,
    ) -> list[Listing]:
        """Return listings from one or more Realt search pages."""
        path = REALT_PATHS.get(request.property_type)
        if path is None:
            return []
        next_url: str | None = self._url(path, {})
        listings: list[Listing] = []
        for page_number in range(max_pages):
            if next_url is None:
                break
            logger.debug(
                "source_page_fetch_started source=realt page=%s url=%s",
                page_number + 1,
                next_url,
            )
            result = await self.search_page(next_url, request.property_type)
            logger.debug(
                "source_page_parsed source=realt page=%s count=%s total=%s "
                "has_next=%s",
                page_number + 1,
                len(result.listings),
                result.total,
                bool(result.next_cursor),
            )
            listings.extend(result.listings)
            next_url = result.next_cursor
            if page_number < max_pages - 1 and delay_seconds > 0:
                await asyncio.sleep(delay_seconds)
        return listings

    async def fetch_listing_detail(self, listing: Listing) -> Listing:
        """Fetch a listing detail page to get richer text and gallery URLs."""
        return parse_realt_detail_page(
            await self.fetch_url(listing.url),
            listing,
            base_url=self._base_url,
        )

    async def search_page(self, url: str, property_type: str):
        """Fetch and parse one Realt search page."""
        html = await self.fetch_url(url)
        try:
            result = parse_realt_search_page(
                html,
                base_url=self._base_url,
                property_type=property_type,
            )
        except Exception:
            if not settings.browser_fetch_enabled:
                raise
            logger.warning("source_parse_failed_using_browser source=realt url=%s", url)
            try:
                result = parse_realt_search_page(
                    await self.fetch_url_with_browser(url),
                    base_url=self._base_url,
                    property_type=property_type,
                )
            except BrowserFetchError as error:
                raise RealtNetworkError(f"Realt request failed: {url}") from error
        if should_retry_empty_result(result):
            logger.warning(
                "source_parse_empty_using_browser source=realt total=%s url=%s",
                result.total,
                url,
            )
            try:
                return parse_realt_search_page(
                    await self.fetch_url_with_browser(url),
                    base_url=self._base_url,
                    property_type=property_type,
                )
            except BrowserFetchError as error:
                raise RealtNetworkError(f"Realt request failed: {url}") from error
        return result

    async def fetch_url(self, url: str) -> str:
        """Fetch an absolute or site-relative URL and return response text."""
        if url.startswith("/"):
            url = f"{self._base_url}{url}"
        last_error: Exception | None = None
        for attempt in range(self._retries + 1):
            started_at = perf_counter()
            logger.debug(
                "source_http_request_started source=realt attempt=%s url=%s "
                "timeout_seconds=%s",
                attempt + 1,
                url,
                self._timeout_seconds,
            )
            try:
                response = await self._client.get(url)
                response.raise_for_status()
                logger.debug(
                    "source_http_request_finished source=realt attempt=%s "
                    "status_code=%s duration_ms=%s response_bytes=%s url=%s",
                    attempt + 1,
                    response.status_code,
                    elapsed_ms(started_at),
                    len(response.content),
                    url,
                )
                return response.text
            except (
                httpx.TimeoutException,
                httpx.NetworkError,
                httpx.HTTPStatusError,
            ) as error:
                last_error = error
                logger.warning(
                    "source_http_request_failed source=realt attempt=%s "
                    "error_type=%s error=%s duration_ms=%s url=%s retrying=%s",
                    attempt + 1,
                    type(error).__name__,
                    error,
                    elapsed_ms(started_at),
                    url,
                    attempt < self._retries,
                )
                if attempt < self._retries:
                    await asyncio.sleep(self._retry_delay_seconds)
                    continue
                break
        if settings.browser_fetch_enabled:
            try:
                return await self.fetch_url_with_browser(url)
            except BrowserFetchError as browser_error:
                last_error = browser_error
        raise RealtNetworkError(f"Realt request failed: {url}") from last_error

    async def fetch_url_with_browser(self, url: str) -> str:
        """Fetch a URL through the configured browser fallback."""
        if url.startswith("/"):
            url = f"{self._base_url}{url}"
        logger.info("source_browser_fetch_started source=realt url=%s", url)
        return await asyncio.to_thread(browser_fetcher.fetch_html, url)

    def _url(self, path: str, _params: dict[str, str]) -> str:
        """Build an absolute URL for a Realt path."""
        return f"{self._base_url}{path}"


def elapsed_ms(started_at: float) -> int:
    """Return elapsed milliseconds from a perf-counter timestamp."""
    return int((perf_counter() - started_at) * 1000)


def should_retry_empty_result(result) -> bool:
    """Return whether an empty search result should be retried in a browser."""
    return (
        settings.browser_fetch_enabled
        and settings.browser_fetch_fallback_on_empty
        and bool(result.total)
        and not result.listings
    )
