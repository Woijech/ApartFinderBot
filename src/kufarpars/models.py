"""Domain models shared by the Kufar parser and Telegram bot.

The classes in this module intentionally contain no scraping or Telegram
knowledge. Keep them small and stable so new parsers can reuse the same output
shape when the project grows beyond real-estate listings.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class ListingImage:
    """A normalized image URL set for one listing photo."""

    gallery_url: str
    thumbnail_url: str | None = None


@dataclass(frozen=True)
class Listing:
    """Normalized listing data extracted from Kufar pages."""

    ad_id: int
    title: str
    url: str
    price_byn: float | None = None
    price_usd: float | None = None
    currency: str | None = None
    address: str | None = None
    rooms: str | None = None
    area_m2: float | None = None
    floor: str | None = None
    total_floors: str | None = None
    metro: list[str] = field(default_factory=list)
    description: str | None = None
    published_at: datetime | None = None
    seller_name: str | None = None
    company_ad: bool = False
    images: list[ListingImage] = field(default_factory=list)
    raw_parameters: dict[str, Any] = field(default_factory=dict)

    @property
    def price_label(self) -> str:
        """Return the most useful human-readable price string."""
        if self.price_usd is not None:
            return f"{self.price_usd:g} $"
        if self.price_byn is not None:
            return f"{self.price_byn:g} BYN"
        return "Договорная"

    @property
    def short_location(self) -> str:
        """Return address and metro in one compact display line."""
        parts = [self.address, ", ".join(self.metro)]
        return " | ".join(part for part in parts if part)


@dataclass(frozen=True)
class SearchResult:
    """A single Kufar search page with pagination metadata."""

    listings: list[Listing]
    total: int | None
    next_cursor: str | None
    search_id: str | None
