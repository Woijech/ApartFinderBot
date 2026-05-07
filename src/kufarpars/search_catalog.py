"""Extensible search-target and filter catalog for bot UI.

Add new categories here first. The Telegram bot reads this catalog to render
buttons, while ``SearchRequest`` remains a generic transport object.
"""

from __future__ import annotations

from dataclasses import dataclass

from kufarpars.client import SearchRequest


@dataclass(frozen=True)
class SearchTarget:
    """A parser target that can be selected in the bot UI."""

    code: str
    title: str
    request_patch: dict[str, object]
    description: str


@dataclass(frozen=True)
class PriceRange:
    """A user-facing price preset for the current bot flow."""

    code: str
    title: str
    min_price: int | None
    max_price: int | None


SEARCH_TARGETS = [
    SearchTarget(
        code="apartment",
        title="Квартира",
        request_patch={"property_type": "apartment"},
        description="Аренда квартир в Минске",
    ),
    SearchTarget(
        code="room",
        title="Комната",
        request_patch={"property_type": "room"},
        description="Аренда комнат в Минске",
    ),
]

PRICE_RANGES = [
    PriceRange("any", "Любая цена", None, None),
    PriceRange("0_150", "до 150 $", 0, 150),
    PriceRange("150_250", "150-250 $", 150, 250),
    PriceRange("250_350", "250-350 $", 250, 350),
    PriceRange("350_500", "350-500 $", 350, 500),
    PriceRange("500_800", "500-800 $", 500, 800),
    PriceRange("800_1200", "800-1200 $", 800, 1200),
]


def default_request() -> SearchRequest:
    """Return the default bot search request."""
    return SearchRequest(city="minsk", deal="rent", currency="USD", sort="newest")


def target_by_code(code: str) -> SearchTarget:
    """Find a search target by internal code."""
    for target in SEARCH_TARGETS:
        if target.code == code:
            return target
    raise ValueError(f"Unknown search target: {code}")


def target_for_request(request: SearchRequest) -> SearchTarget:
    """Find the catalog target matching a request."""
    for target in SEARCH_TARGETS:
        if target.request_patch.get("property_type") == request.property_type:
            return target
    return SEARCH_TARGETS[0]


def price_range_by_code(code: str) -> PriceRange:
    """Find a price preset by callback code."""
    for price_range in PRICE_RANGES:
        if price_range.code == code:
            return price_range
    raise ValueError(f"Unknown price range: {code}")
