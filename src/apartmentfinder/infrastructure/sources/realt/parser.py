"""HTML parser for public Realt.by rental pages.

Realt renders enough listing data in plain HTML for the bot to normalize search
cards without a browser. The parser deliberately uses text and URL patterns
instead of brittle generated CSS class names.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup, Tag

from apartmentfinder.domain.models import Listing, ListingImage, SearchResult

REALT_BASE_URL = "https://realt.by"
REALT_SOURCE = "realt"
DATE_TIMEZONE = ZoneInfo("Europe/Minsk")
ID_RE = re.compile(r"\bID\s*(\d+)\b", re.IGNORECASE)
OBJECT_ID_RE = re.compile(r"/object/(?P<id>\d+)/?")
USD_RE = re.compile(r"≈\s*([\d\s]+)\s*\$")
BYN_RE = re.compile(r"([\d\s]+)\s*р\./мес\.", re.IGNORECASE)
DATE_ID_RE = re.compile(
    r"(?:(?:\d+)\s+час(?:а|ов)?\s+назад|"
    r"вчера,\s*\d{1,2}:\d{2}|"
    r"\d{2}\.\d{2}\.\d{4})?\s*ID\s*(?P<id>\d+)",
    re.IGNORECASE,
)
SPECS_RE = re.compile(
    r"(?:(?P<rooms>\d+)\s*комн\.)?\s*"
    r"(?P<area>\d+(?:[.,]\d+)?)\s*м²\s*"
    r"(?P<floor>\d+)\s*/\s*(?P<total>\d+)\s*этаж",
    re.IGNORECASE,
)
ROOM_SPECS_RE = re.compile(
    r"Комната\s+(?P<area>\d+(?:[.,]\d+)?)\s*м²\s*"
    r"(?P<floor>\d+)\s*/\s*(?P<total>\d+)\s*этаж",
    re.IGNORECASE,
)
logger = logging.getLogger(__name__)


def parse_realt_search_page(
    html: str,
    *,
    base_url: str = REALT_BASE_URL,
    property_type: str = "apartment",
    now: datetime | None = None,
) -> SearchResult:
    """Parse a Realt search page into normalized listing cards."""
    soup = BeautifulSoup(html, "html.parser")
    listings = _parse_dom_cards(
        soup,
        base_url=base_url,
        property_type=property_type,
        now=now,
    )
    text_listings = _parse_text_blocks(
        soup,
        base_url=base_url,
        property_type=property_type,
        now=now,
    )
    if len(text_listings) > len(listings):
        text_ids = {listing.ad_id for listing in text_listings}
        listings = text_listings + [
            listing for listing in listings if listing.ad_id not in text_ids
        ]
    else:
        seen_ids = {listing.ad_id for listing in listings}
        listings.extend(
            listing for listing in text_listings if listing.ad_id not in seen_ids
        )
    total = _parse_total(soup)
    if total and not listings:
        logger.warning(
            "source_parse_suspicious source=realt total=%s parsed=0",
            total,
        )
    return SearchResult(
        listings=listings,
        total=total,
        next_cursor=_parse_next_page(soup, base_url),
        search_id=None,
    )


def _parse_dom_cards(
    soup: BeautifulSoup,
    *,
    base_url: str,
    property_type: str,
    now: datetime | None,
) -> list[Listing]:
    """Parse cards from anchors and ID text nodes before falling back to text."""
    listings = []
    seen_ids: set[int] = set()
    for anchor in soup.find_all("a", href=True):
        match = OBJECT_ID_RE.search(str(anchor["href"]))
        if not match:
            continue
        ad_id = int(match.group(1))
        if ad_id in seen_ids:
            continue
        card = _listing_card(anchor)
        if not _looks_like_card(card):
            continue
        listing = _parse_card(
            card,
            ad_id=ad_id,
            base_url=base_url,
            property_type=property_type,
            now=now,
        )
        listings.append(listing)
        seen_ids.add(ad_id)

    for id_node in soup.find_all(string=ID_RE):
        match = ID_RE.search(str(id_node))
        if not match:
            continue
        ad_id = int(match.group(1))
        if ad_id in seen_ids:
            continue
        card = _listing_card(id_node)
        if not _looks_like_card(card):
            continue
        listing = _parse_card(
            card,
            ad_id=ad_id,
            base_url=base_url,
            property_type=property_type,
            now=now,
        )
        listings.append(listing)
        seen_ids.add(ad_id)
    return listings


def _parse_text_blocks(
    soup: BeautifulSoup,
    *,
    base_url: str,
    property_type: str,
    now: datetime | None,
) -> list[Listing]:
    """Parse live Realt pages when generated DOM classes are hard to trust."""
    lines = _clean_lines(soup.get_text("\n", strip=True).splitlines())
    listings = []
    seen_ids: set[int] = set()
    previous_boundary = 0
    index = 0
    while index < len(lines):
        boundary = _id_boundary(lines, index)
        if boundary is None:
            index += 1
            continue
        ad_id, id_index, next_index = boundary
        if ad_id in seen_ids:
            previous_boundary = next_index
            index = next_index
            continue
        block_lines = _listing_text_block(lines, previous_boundary, index)
        previous_boundary = next_index
        index = next_index
        if not block_lines:
            continue
        listing_lines = block_lines + lines[id_index:next_index]
        listing = _parse_text_block(
            listing_lines,
            ad_id=ad_id,
            base_url=base_url,
            property_type=property_type,
            now=now,
        )
        listings.append(listing)
        seen_ids.add(ad_id)
    return listings


def parse_realt_detail_page(
    html: str,
    fallback: Listing,
    *,
    base_url: str = REALT_BASE_URL,
) -> Listing:
    """Parse a Realt detail page and merge richer text/images into a listing."""
    soup = BeautifulSoup(html, "html.parser")
    description = _meta_content(soup, "description") or fallback.description
    title = _title_from_detail(soup) or fallback.title
    images = _detail_images(soup, base_url) or fallback.images
    canonical = _canonical_url(soup, base_url) or fallback.url
    return replace(
        fallback,
        title=title,
        url=canonical,
        description=description,
        images=images,
    )


def stable_realt_id(url: str) -> int:
    """Return a stable positive 63-bit id when Realt URL has no visible id."""
    digest = hashlib.blake2b(url.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") & ((1 << 63) - 1)


def _listing_card(node: object) -> Tag:
    """Find the nearest ancestor that looks like a complete listing card."""
    current = getattr(node, "parent", None)
    while isinstance(current, Tag):
        text = current.get_text(" ", strip=True)
        if (
            ID_RE.search(text)
            and ("$/мес" in text or "р./мес" in text)
            and ("Минск" in text or "м²" in text)
        ):
            return current
        current = current.parent
    return BeautifulSoup("", "html.parser")


def _looks_like_card(card: Tag) -> bool:
    """Return whether a DOM fragment has enough data to be a listing card."""
    text = card.get_text(" ", strip=True)
    return bool(
        ID_RE.search(text) or OBJECT_ID_RE.search(str(card))
    ) and bool(BYN_RE.search(text) or USD_RE.search(text) or _parse_specs(text, "room"))


def _parse_card(
    card: Tag,
    *,
    ad_id: int,
    base_url: str,
    property_type: str,
    now: datetime | None,
) -> Listing:
    """Parse one listing card from a Realt search page."""
    text = card.get_text("\n", strip=True)
    lines = _clean_lines(text.splitlines())
    url = _listing_url(card, ad_id, base_url, property_type)
    specs = _parse_specs(text, property_type)
    title = _listing_title(card, lines, specs, property_type)
    return Listing(
        ad_id=ad_id or stable_realt_id(url),
        title=title,
        url=url,
        source=REALT_SOURCE,
        price_byn=_money_from_match(BYN_RE.search(text)),
        price_usd=_money_from_match(USD_RE.search(text)),
        currency="USD",
        address=_first_matching_line(lines, ("г.", "Минск")),
        rooms=specs.get("rooms"),
        area_m2=_float_or_none(specs.get("area")),
        floor=specs.get("floor"),
        total_floors=specs.get("total"),
        metro=_metro_lines(lines),
        description=_description(lines),
        published_at=_published_at(text, now),
        company_ad="Агентство" in text,
        images=_card_images(card, base_url),
        raw_parameters={"source": REALT_SOURCE},
    )


def _parse_text_block(
    lines: list[str],
    *,
    ad_id: int,
    base_url: str,
    property_type: str,
    now: datetime | None,
) -> Listing:
    """Parse one normalized text block into a Realt listing."""
    text = "\n".join(lines)
    specs = _parse_specs(text, property_type)
    return Listing(
        ad_id=ad_id,
        title=_text_block_title(lines, specs, property_type),
        url=_fallback_listing_url(ad_id, base_url, property_type),
        source=REALT_SOURCE,
        price_byn=_money_from_match(BYN_RE.search(text)),
        price_usd=_money_from_match(USD_RE.search(text)),
        currency="USD",
        address=_first_matching_line(lines, ("г.", "Минск")),
        rooms=specs.get("rooms"),
        area_m2=_float_or_none(specs.get("area")),
        floor=specs.get("floor"),
        total_floors=specs.get("total"),
        metro=_metro_lines(lines),
        description=_description(lines),
        published_at=_published_at(text, now),
        company_ad="Агентство" in text,
        images=[],
        raw_parameters={"source": REALT_SOURCE, "parser": "text_block"},
    )


def _parse_specs(text: str, property_type: str) -> dict[str, str | None]:
    """Extract room count, area, and floor values from card text."""
    match = ROOM_SPECS_RE.search(text) if property_type == "room" else None
    match = match or SPECS_RE.search(text)
    if not match:
        return {}
    groups = match.groupdict()
    rooms = groups.get("rooms")
    if property_type == "room" and rooms is None:
        rooms = "1"
    return {
        "rooms": rooms,
        "area": groups.get("area"),
        "floor": groups.get("floor"),
        "total": groups.get("total"),
    }


def _listing_url(card: Tag, ad_id: int, base_url: str, property_type: str) -> str:
    """Return canonical listing URL from a card or build a stable fallback."""
    for anchor in card.find_all("a", href=True):
        href = str(anchor["href"])
        if str(ad_id) in href:
            return urljoin(base_url, href).split("?", maxsplit=1)[0]
    return _fallback_listing_url(ad_id, base_url, property_type)


def _fallback_listing_url(ad_id: int, base_url: str, property_type: str) -> str:
    """Build the canonical Realt listing URL from source type and id."""
    target = "room-for-long" if property_type == "room" else "flat-for-long"
    return f"{base_url.rstrip('/')}/rent/{target}/object/{ad_id}/"


def _listing_title(
    card: Tag,
    lines: list[str],
    specs: dict[str, str | None],
    property_type: str,
) -> str:
    """Pick a readable card title, falling back to a generic property label."""
    for anchor in card.find_all("a"):
        title = " ".join(anchor.stripped_strings)
        if (
            title
            and not ID_RE.search(title)
            and "$" not in title
            and "р./мес" not in title
        ):
            return title
    ignored = {
        "Показать больше",
        "Контакты",
        "Написать",
        "Контактное лицо",
        "Агентство",
    }
    for line in lines:
        if line in ignored or ID_RE.search(line):
            continue
        if "объявлен" in line:
            continue
        if "$/мес" in line or "р./мес" in line or "Минск" in line or "м²" in line:
            continue
        if specs.get("rooms") and line.startswith(f"{specs['rooms']} комн."):
            continue
        return line
    return "Комната" if property_type == "room" else "Квартира"


def _description(lines: list[str]) -> str | None:
    """Return visible card description without price/contact chrome."""
    ignored_fragments = (
        "р./мес",
        "$/мес",
        "Показать больше",
        "Контакты",
        "Написать",
        "Контактное лицо",
        "Агентство",
        "объявлен",
    )
    result = []
    for line in lines:
        if line.casefold() == "id" or line.isdigit():
            continue
        if ID_RE.search(line) or any(
            fragment in line for fragment in ignored_fragments
        ):
            continue
        if line in _metro_lines(lines) or _looks_like_relative_date(line):
            continue
        if line.startswith("г.") or "м²" in line or line.startswith("≈"):
            continue
        result.append(line)
    return " ".join(result).strip() or None


def _published_at(text: str, now: datetime | None) -> datetime | None:
    """Parse Realt card dates such as ``2 часа назад`` or ``07.05.2026``."""
    now = now or datetime.now(DATE_TIMEZONE)
    if now.tzinfo is None:
        now = now.replace(tzinfo=DATE_TIMEZONE)
    if match := re.search(r"(\d+)\s+час", text):
        return (now - timedelta(hours=int(match.group(1)))).astimezone(UTC)
    if match := re.search(r"вчера,\s*(\d{1,2}):(\d{2})", text, re.IGNORECASE):
        yesterday = now.astimezone(DATE_TIMEZONE) - timedelta(days=1)
        return yesterday.replace(
            hour=int(match.group(1)),
            minute=int(match.group(2)),
            second=0,
            microsecond=0,
        ).astimezone(UTC)
    if match := re.search(r"(\d{2})\.(\d{2})\.(\d{4})", text):
        day, month, year = (int(part) for part in match.groups())
        return datetime(year, month, day, tzinfo=DATE_TIMEZONE).astimezone(UTC)
    return None


def _metro_lines(lines: list[str]) -> list[str]:
    """Extract likely metro labels from card lines."""
    result = []
    for line in lines:
        if "минут" in line.lower() or line in {
            "Немига",
            "Кунцевщина",
            "Пушкинская",
            "Малиновка",
            "Фрунзенская",
            "Партизанская",
            "Пролетарская",
            "Московская",
            "Октябрьская",
            "Петровщина",
        }:
            result.append(line)
    return result


def _card_images(card: Tag, base_url: str) -> list[ListingImage]:
    """Collect image URLs from a search card."""
    images = []
    for image in card.find_all("img"):
        src = image.get("src") or image.get("data-src")
        if not src:
            continue
        images.append(ListingImage(gallery_url=urljoin(base_url, str(src))))
    return list(dict.fromkeys(images))


def _detail_images(soup: BeautifulSoup, base_url: str) -> list[ListingImage]:
    """Collect OpenGraph and page images from a detail page."""
    urls = []
    for meta in soup.find_all("meta", property="og:image"):
        content = meta.get("content")
        if content:
            urls.append(urljoin(base_url, str(content)))
    return [ListingImage(gallery_url=url) for url in dict.fromkeys(urls)]


def _parse_total(soup: BeautifulSoup) -> int | None:
    """Parse total listing count from page text."""
    match = re.search(r"(\d+)\s+объявлен", soup.get_text(" ", strip=True))
    return int(match.group(1)) if match else None


def _parse_next_page(soup: BeautifulSoup, base_url: str) -> str | None:
    """Return next page URL when Realt exposes one."""
    link = soup.find("a", attrs={"rel": "next"})
    if isinstance(link, Tag) and link.get("href"):
        return urljoin(base_url, str(link["href"]))
    return None


def _canonical_url(soup: BeautifulSoup, base_url: str) -> str | None:
    """Return canonical URL from a detail page."""
    link = soup.find("link", rel="canonical")
    if isinstance(link, Tag) and link.get("href"):
        return urljoin(base_url, str(link["href"]))
    return None


def _title_from_detail(soup: BeautifulSoup) -> str | None:
    """Return a detail title from OpenGraph or document title."""
    title = _meta_content(soup, "og:title")
    if title:
        return title
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    return None


def _meta_content(soup: BeautifulSoup, name: str) -> str | None:
    """Read a meta tag by name or OpenGraph property."""
    meta = soup.find("meta", attrs={"name": name}) or soup.find(
        "meta",
        property=name,
    )
    if isinstance(meta, Tag) and meta.get("content"):
        return str(meta["content"]).strip()
    return None


def _money_from_match(match: re.Match[str] | None) -> float | None:
    """Parse a money amount from a regex match."""
    if not match:
        return None
    return float("".join(match.group(1).split()))


def _float_or_none(value: str | None) -> float | None:
    """Parse a decimal string into a float."""
    if not value:
        return None
    return float(value.replace(",", "."))


def _first_matching_line(lines: list[str], needles: tuple[str, ...]) -> str | None:
    """Return first line that contains one of the requested fragments."""
    for line in lines:
        if any(needle in line for needle in needles):
            return line
    return None


def _clean_lines(lines: list[str]) -> list[str]:
    """Normalize whitespace and remove duplicate adjacent lines."""
    result = []
    for line in lines:
        cleaned = " ".join(line.split())
        if cleaned and (not result or result[-1] != cleaned):
            result.append(cleaned)
    return result


def _listing_text_block(
    lines: list[str],
    previous_boundary: int,
    id_index: int,
) -> list[str]:
    """Return the likely listing text slice that ends before an ID line."""
    start = previous_boundary
    for index in range(id_index - 1, previous_boundary - 1, -1):
        candidate = "\n".join(lines[index:id_index])
        if BYN_RE.search(candidate) or USD_RE.search(candidate):
            start = index
    block = lines[start:id_index]
    if not any(BYN_RE.search(line) or USD_RE.search(line) for line in block):
        return []
    return block


def _text_block_title(
    lines: list[str],
    specs: dict[str, str | None],
    property_type: str,
) -> str:
    """Pick a title for a text-only Realt listing block."""
    title = _listing_title(
        BeautifulSoup("", "html.parser"),
        lines,
        specs,
        property_type,
    )
    if title not in {"Комната", "Квартира"}:
        return title
    address = _first_matching_line(lines, ("г.", "Минск"))
    if property_type == "room":
        return f"Комната, {address}" if address else "Комната"
    return f"Квартира, {address}" if address else "Квартира"


def _id_boundary(lines: list[str], index: int) -> tuple[int, int, int] | None:
    """Return ad id and consumed line range for a Realt ID boundary."""
    line = lines[index]
    if match := DATE_ID_RE.search(line):
        return int(match.group("id")), index, index + 1
    if line.casefold() == "id" and index + 1 < len(lines):
        next_line = lines[index + 1]
        if next_line.isdigit():
            return int(next_line), index, index + 2
    return None


def _looks_like_relative_date(line: str) -> bool:
    """Return whether a line is a visible relative publish date."""
    return bool(
        re.search(r"\d+\s+час(?:а|ов)?\s+назад", line, re.IGNORECASE)
        or re.search(r"вчера,\s*\d{1,2}:\d{2}", line, re.IGNORECASE)
    )
