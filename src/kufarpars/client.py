"""HTTP client and search request builder for Kufar.

The rest of the project should go through this module for network access. That
keeps throttling, headers, pagination, detail-page enrichment, and future parser
targets in one place.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from time import sleep
from urllib.parse import urlencode, urlparse

import httpx

from kufarpars.config import settings
from kufarpars.models import Listing
from kufarpars.parser import parse_detail_page, parse_search_page

ROOM_PATHS = {1: "1k", 2: "2k", 3: "3k", 4: "4k"}
DEAL_PATHS = {"rent": "snyat", "buy": "kupit"}
PROPERTY_PATHS = {"apartment": "kvartiru", "room": "komnatu"}
SORT_VALUES = {"newest": None, "cheap": "prc.a", "expensive": "prc.d"}


class KufarNetworkError(RuntimeError):
    """Raised when Kufar cannot be reached after retry attempts."""


@dataclass(frozen=True)
class SearchRequest:
    """A high-level search request that can be converted into a Kufar URL."""

    city: str = "minsk"
    deal: str = "rent"
    property_type: str = "apartment"
    rooms: int | None = None
    min_price: int | None = None
    max_price: int | None = None
    currency: str = "USD"
    text: str | None = None
    district: str | None = None
    metro: str | None = None
    include_keywords: list[str] = field(default_factory=list)
    exclude_keywords: list[str] = field(default_factory=list)
    sort: str = "newest"
    size: int = 30
    extra_params: dict[str, str] = field(default_factory=dict)

    def path(self) -> str:
        """Build the friendly Kufar path for the configured search target."""
        deal_path = DEAL_PATHS[self.deal]
        property_path = PROPERTY_PATHS[self.property_type]
        parts = ["l", self.city, deal_path, property_path]
        if self.property_type == "apartment" and self.rooms in ROOM_PATHS:
            parts.append(ROOM_PATHS[self.rooms])
        return "/" + "/".join(parts)

    def params(self) -> dict[str, str]:
        """Build query parameters for the configured filters."""
        params = {
            "cur": self.currency,
            "size": str(self.size),
        }
        if self.text:
            params["query"] = self.text
        if self.min_price is not None or self.max_price is not None:
            lower = self.min_price if self.min_price is not None else 0
            upper = self.max_price if self.max_price is not None else 1_000_000_000
            params["prc"] = f"r:{lower},{upper}"
        sort_value = SORT_VALUES[self.sort]
        if sort_value:
            params["sort"] = sort_value
        params.update(self.extra_params)
        return params


class KufarClient:
    """Synchronous Kufar client used by background bot jobs."""

    def __init__(
        self,
        base_url: str = settings.realty_url,
        timeout_seconds: float = settings.timeout_seconds,
        retries: int = settings.request_retries,
        retry_delay_seconds: float = settings.request_retry_delay_seconds,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._retries = max(retries, 0)
        self._retry_delay_seconds = max(retry_delay_seconds, 0)
        self._client = httpx.Client(
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

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        self._client.close()

    def __enter__(self) -> KufarClient:
        """Enter a context-managed client session."""
        return self

    def __exit__(self, *_args: object) -> None:
        """Close the client session when leaving a context manager."""
        self.close()

    def search_pages(
        self,
        request: SearchRequest,
        max_pages: int = 1,
        delay_seconds: float = 1.0,
    ) -> Iterable[Listing]:
        """Yield listings from one or more Kufar search pages."""
        params = request.params()
        cursor: str | None = None

        for page_number in range(max_pages):
            page_params = params | ({"cursor": cursor} if cursor else {})
            result = self.search_page(request.path(), page_params)
            yield from result.listings

            cursor = result.next_cursor
            if not cursor:
                break
            if page_number < max_pages - 1 and delay_seconds > 0:
                sleep(delay_seconds)

    def search_page(self, path: str, params: dict[str, str]):
        """Fetch and parse one Kufar search page."""
        return parse_search_page(self.fetch_html(path, params))

    def fetch_listing_detail(self, listing: Listing) -> Listing:
        """Fetch a listing detail page to get full description and gallery URLs."""
        return parse_detail_page(self.fetch_url(listing.url))

    def fetch_html(self, path: str, params: dict[str, str]) -> str:
        """Fetch a Kufar path with query parameters and return response text."""
        url = self._url(path, params)
        return self.fetch_url(url)

    def fetch_url(self, url: str) -> str:
        """Fetch an absolute or site-relative URL and return response text."""
        if url.startswith("/"):
            url = f"{self._base_url}{url}"
        last_error: Exception | None = None
        for attempt in range(self._retries + 1):
            try:
                response = self._client.get(url)
                response.raise_for_status()
                return response.text
            except (httpx.TimeoutException, httpx.NetworkError) as error:
                last_error = error
                if attempt < self._retries:
                    sleep(self._retry_delay_seconds)
                    continue
                break
        raise KufarNetworkError(f"Kufar request failed: {url}") from last_error

    def _url(self, path: str, params: dict[str, str]) -> str:
        """Build an absolute URL for a Kufar path and query dictionary."""
        query = urlencode(params)
        return f"{self._base_url}{path}?{query}"

    @staticmethod
    def path_from_url(url: str) -> str:
        """Return only the path part from an absolute Kufar URL."""
        return urlparse(url).path
