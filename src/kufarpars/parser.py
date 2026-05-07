"""HTML and JSON parsers for public Kufar pages.

Kufar renders listing data into the Next.js ``__NEXT_DATA__`` script. The
parser reads that JSON instead of relying on CSS classes, which keeps the code
less brittle and makes it easier to add new listing categories later.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from bs4 import BeautifulSoup

from kufarpars.models import Listing, ListingImage, SearchResult


class KufarParseError(ValueError):
    """Raised when Kufar markup does not contain expected listing data."""


def parse_search_page(html: str) -> SearchResult:
    """Parse a Kufar search-result page into normalized listings."""
    data = extract_next_data(html)
    listing_state = data["props"]["initialState"]["listing"]
    ads = listing_state.get("ads") or []

    return SearchResult(
        listings=[parse_listing(ad) for ad in ads],
        total=_parse_int(listing_state.get("total")),
        next_cursor=_next_cursor(listing_state.get("pagination") or []),
        search_id=listing_state.get("searchId"),
    )


def extract_next_data(html: str) -> dict[str, Any]:
    """Extract the embedded Next.js JSON payload from a Kufar HTML page."""
    soup = BeautifulSoup(html, "html.parser")
    script = soup.find("script", id="__NEXT_DATA__")
    if script is None or not script.string:
        raise KufarParseError("Could not find __NEXT_DATA__ in Kufar page.")
    return json.loads(script.string)


def parse_listing(ad: dict[str, Any]) -> Listing:
    """Parse a raw Kufar ad dictionary from search or detail JSON."""
    parameters = _parameters_by_name(ad.get("ad_parameters") or [])
    account_parameters = _parameters_by_name(ad.get("account_parameters") or [])

    return Listing(
        ad_id=int(ad["ad_id"]),
        title=ad.get("subject") or "",
        url=_clean_url(ad.get("ad_link") or ""),
        price_byn=_price_from_cents(ad.get("price_byn")),
        price_usd=_price_from_cents(ad.get("price_usd")),
        currency=ad.get("currency"),
        address=_string_or_none(account_parameters.get("address")),
        rooms=_string_or_none(parameters.get("rooms")),
        area_m2=_float_or_none(parameters.get("size")),
        floor=_first_string(parameters.get("floor")),
        total_floors=_first_string(parameters.get("re_number_floors")),
        metro=_string_list(parameters.get("metro")),
        description=ad.get("body_short") or ad.get("body"),
        published_at=_datetime_or_none(ad.get("list_time")),
        seller_name=_string_or_none(account_parameters.get("name")),
        company_ad=bool(ad.get("company_ad")),
        images=_parse_images(ad.get("images")),
        raw_parameters=parameters,
    )


def parse_detail_page(html: str) -> Listing:
    """Parse a Kufar detail page and prefer its full description and images."""
    data = extract_next_data(html)
    ad_view = data["props"]["initialState"]["adView"]["data"]
    initial = ad_view.get("initial") or {}
    listing = parse_listing(initial)
    return Listing(
        ad_id=listing.ad_id,
        title=ad_view.get("title") or listing.title,
        url=_clean_url(ad_view.get("adViewLink") or listing.url),
        price_byn=listing.price_byn,
        price_usd=listing.price_usd,
        currency=listing.currency,
        address=ad_view.get("addressWithDistrict") or ad_view.get("address"),
        rooms=listing.rooms,
        area_m2=listing.area_m2,
        floor=listing.floor,
        total_floors=listing.total_floors,
        metro=_string_list(ad_view.get("metro")) or listing.metro,
        description=ad_view.get("body") or ad_view.get("description"),
        published_at=listing.published_at,
        seller_name=ad_view.get("userName") or listing.seller_name,
        company_ad=bool(ad_view.get("isCompanyAd")),
        images=_parse_detail_images(ad_view) or listing.images,
        raw_parameters=listing.raw_parameters,
    )


def _parameters_by_name(parameters: list[dict[str, Any]]) -> dict[str, Any]:
    """Index Kufar parameter values by their internal parameter name."""
    result: dict[str, Any] = {}
    for parameter in parameters:
        name = parameter.get("p")
        if not name:
            continue
        result[name] = parameter.get("vl") or parameter.get("v")
    return result


def _next_cursor(pagination: list[dict[str, Any]]) -> str | None:
    """Return the cursor token for the next search page if Kufar provides it."""
    for page in pagination:
        if page.get("label") == "next":
            return page.get("token")
    return None


def _clean_url(url: str) -> str:
    """Strip tracking query parameters from a listing URL."""
    return url.split("?", maxsplit=1)[0]


def _price_from_cents(value: Any) -> float | None:
    """Convert Kufar cent-style price values into normal money values."""
    parsed = _parse_int(value)
    if parsed is None:
        return None
    return parsed / 100


def _parse_images(images: Any) -> list[ListingImage]:
    """Convert raw search-page image dictionaries into gallery URLs."""
    if not isinstance(images, list):
        return []
    result = []
    for image in images:
        path = image.get("path") if isinstance(image, dict) else None
        if not path:
            continue
        result.append(
            ListingImage(
                gallery_url=f"https://rms.kufar.by/v1/gallery/{path}",
                thumbnail_url=f"https://rms.kufar.by/v1/list_thumbs_2x/{path}",
            )
        )
    return result


def _parse_detail_images(ad_view: dict[str, Any]) -> list[ListingImage]:
    """Convert detail-page gallery URLs into normalized image objects."""
    gallery = (ad_view.get("images") or {}).get("gallery")
    thumbnails = (ad_view.get("images") or {}).get("thumbnails") or []
    if not isinstance(gallery, list):
        gallery = (ad_view.get("gallery") or {}).get("images")
    if not isinstance(gallery, list):
        return []
    return [
        ListingImage(
            gallery_url=str(url),
            thumbnail_url=str(thumbnails[index]) if index < len(thumbnails) else None,
        )
        for index, url in enumerate(gallery)
    ]


def _parse_int(value: Any) -> int | None:
    """Parse an integer from Kufar values that may arrive as strings."""
    if value in (None, ""):
        return None
    try:
        return int(str(value).replace(" ", ""))
    except ValueError:
        return None


def _float_or_none(value: Any) -> float | None:
    """Parse a float from a Kufar value or return None."""
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _datetime_or_none(value: str | None) -> datetime | None:
    """Parse Kufar ISO datetime strings."""
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _first_string(value: Any) -> str | None:
    """Return the first list item as text, or convert a scalar to text."""
    if isinstance(value, list):
        return str(value[0]) if value else None
    return _string_or_none(value)


def _string_list(value: Any) -> list[str]:
    """Normalize scalar/list values into a list of strings."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _string_or_none(value: Any) -> str | None:
    """Normalize scalar/list values into optional display text."""
    if value in (None, ""):
        return None
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)
