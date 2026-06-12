"""HTTP client and search request builder for the Kufar source adapter."""

from __future__ import annotations

import asyncio
import logging
from time import perf_counter
from urllib.parse import urlencode, urlparse

import httpx

from apartmentfinder.domain.models import Listing, SearchRequest
from apartmentfinder.infrastructure import browser_fetcher
from apartmentfinder.infrastructure.browser_fetcher import BrowserFetchError
from apartmentfinder.infrastructure.config import settings
from apartmentfinder.infrastructure.rate_limiter import (
    BrowserFallbackRateLimitError,
    source_rate_limiter,
)
from apartmentfinder.infrastructure.sources.kufar.parser import (
    parse_detail_page,
    parse_search_page,
)

ROOM_PATHS = {1: "1k", 2: "2k", 3: "3k", 4: "4k"}
DEAL_PATHS = {"rent": "snyat", "buy": "kupit"}
PROPERTY_PATHS = {"apartment": "kvartiru", "room": "komnatu"}
SORT_VALUES = {"newest": None, "cheap": "prc.a", "expensive": "prc.d"}
logger = logging.getLogger(__name__)


class KufarNetworkError(RuntimeError):
    """Raised when Kufar cannot be reached after retry attempts."""


class KufarClient:
    """Asynchronous Kufar client used by background bot jobs."""

    def __init__(
        self,
        base_url: str = settings.kufar_base_url,
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

    async def __aenter__(self) -> KufarClient:
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
        """Return listings from one or more Kufar search pages."""
        params = kufar_params(request)
        cursor: str | None = None
        listings: list[Listing] = []

        for page_number in range(max_pages):
            page_params = params | ({"cursor": cursor} if cursor else {})
            logger.debug(
                "source_page_fetch_started source=kufar page=%s path=%s params=%s",
                page_number + 1,
                kufar_path(request),
                sorted(page_params),
            )
            result = await self.search_page(kufar_path(request), page_params)
            logger.debug(
                "source_page_parsed source=kufar page=%s count=%s total=%s "
                "has_next=%s",
                page_number + 1,
                len(result.listings),
                result.total,
                bool(result.next_cursor),
            )
            listings.extend(result.listings)

            cursor = result.next_cursor
            if not cursor:
                break
            if page_number < max_pages - 1 and delay_seconds > 0:
                await asyncio.sleep(delay_seconds)
        return listings

    async def search_page(self, path: str, params: dict[str, str]):
        """Fetch and parse one Kufar search page."""
        url = self._url(path, params)
        html = await self.fetch_url(url)
        try:
            result = parse_search_page(html)
        except Exception:
            if not settings.browser_fetch_enabled:
                raise
            logger.warning("source_parse_failed_using_browser source=kufar url=%s", url)
            try:
                result = parse_search_page(await self.fetch_url_with_browser(url))
            except (BrowserFetchError, BrowserFallbackRateLimitError) as error:
                source_rate_limiter.record_error(
                    "kufar",
                    settings.source_limit("kufar"),
                )
                raise KufarNetworkError(f"Kufar request failed: {url}") from error
        if should_retry_empty_result(result):
            logger.warning(
                "source_parse_empty_using_browser source=kufar total=%s url=%s",
                result.total,
                url,
            )
            try:
                return parse_search_page(await self.fetch_url_with_browser(url))
            except (BrowserFetchError, BrowserFallbackRateLimitError) as error:
                source_rate_limiter.record_error(
                    "kufar",
                    settings.source_limit("kufar"),
                )
                raise KufarNetworkError(f"Kufar request failed: {url}") from error
        return result

    async def fetch_listing_detail(self, listing: Listing) -> Listing:
        """Fetch a listing detail page to get full description and gallery URLs."""
        return parse_detail_page(await self.fetch_url(listing.url))

    async def fetch_html(self, path: str, params: dict[str, str]) -> str:
        """Fetch a Kufar path with query parameters and return response text."""
        url = self._url(path, params)
        return await self.fetch_url(url)

    async def fetch_url(self, url: str) -> str:
        """Fetch an absolute or site-relative URL and return response text."""
        if url.startswith("/"):
            url = f"{self._base_url}{url}"
        last_error: Exception | None = None
        for attempt in range(self._retries + 1):
            await source_rate_limiter.acquire_request(
                "kufar",
                settings.source_limit("kufar"),
            )
            started_at = perf_counter()
            logger.debug(
                "source_http_request_started source=kufar attempt=%s url=%s "
                "timeout_seconds=%s",
                attempt + 1,
                url,
                self._timeout_seconds,
            )
            try:
                response = await self._client.get(url)
                response.raise_for_status()
                logger.debug(
                    "source_http_request_finished source=kufar attempt=%s "
                    "status_code=%s duration_ms=%s response_bytes=%s url=%s",
                    attempt + 1,
                    response.status_code,
                    elapsed_ms(started_at),
                    len(response.content),
                    url,
                )
                source_rate_limiter.record_success("kufar")
                return response.text
            except (
                httpx.TimeoutException,
                httpx.NetworkError,
                httpx.HTTPStatusError,
            ) as error:
                last_error = error
                logger.warning(
                    "source_http_request_failed source=kufar attempt=%s "
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
            except (
                BrowserFetchError,
                BrowserFallbackRateLimitError,
            ) as browser_error:
                last_error = browser_error
        source_rate_limiter.record_error("kufar", settings.source_limit("kufar"))
        raise KufarNetworkError(f"Kufar request failed: {url}") from last_error

    async def fetch_url_with_browser(self, url: str) -> str:
        """Fetch a URL through the configured browser fallback."""
        if url.startswith("/"):
            url = f"{self._base_url}{url}"
        await source_rate_limiter.acquire_browser_fallback(
            "kufar",
            settings.source_limit("kufar"),
        )
        logger.info("source_browser_fetch_started source=kufar url=%s", url)
        return await asyncio.to_thread(browser_fetcher.fetch_html, url)

    def _url(self, path: str, params: dict[str, str]) -> str:
        """Build an absolute URL for a Kufar path and query dictionary."""
        query = urlencode(params)
        return f"{self._base_url}{path}?{query}"

    @staticmethod
    def path_from_url(url: str) -> str:
        """Return only the path part from an absolute Kufar URL."""
        return urlparse(url).path


def kufar_path(request: SearchRequest) -> str:
    """Build the friendly Kufar path for a source-neutral search request."""
    deal_path = DEAL_PATHS[request.deal]
    property_path = PROPERTY_PATHS[request.property_type]
    parts = ["l", request.city, deal_path, property_path]
    if request.property_type == "apartment" and request.rooms in ROOM_PATHS:
        parts.append(ROOM_PATHS[request.rooms])
    return "/" + "/".join(parts)


def kufar_params(request: SearchRequest) -> dict[str, str]:
    """Build Kufar query parameters for a source-neutral search request."""
    params = {
        "cur": request.currency,
        "size": str(request.size),
    }
    if request.text:
        params["query"] = request.text
    if request.min_price is not None or request.max_price is not None:
        lower = request.min_price if request.min_price is not None else 0
        upper = request.max_price if request.max_price is not None else 1_000_000_000
        params["prc"] = f"r:{lower},{upper}"
    sort_value = SORT_VALUES[request.sort]
    if sort_value:
        params["sort"] = sort_value
    params.update(request.extra_params)
    return params


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
