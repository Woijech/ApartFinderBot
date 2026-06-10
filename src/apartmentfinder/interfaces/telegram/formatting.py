"""Telegram presentation helpers for parsed listings.

This module owns message text, captions, and image selection. Keeping formatting
outside handlers makes the bot easier to extend with new categories and keeps
Telegram-specific limits in one place.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apartmentfinder.domain.models import Listing
from apartmentfinder.infrastructure.config import settings

TELEGRAM_MESSAGE_LIMIT = 4096
TELEGRAM_CAPTION_LIMIT = 1024
TELEGRAM_TRIM_SUFFIX = "\n..."
SOURCE_LABELS = {"kufar": "Kufar", "realt": "Realt"}


@dataclass(frozen=True)
class ListingPresentation:
    """Prepared Telegram payload for one listing."""

    caption: str
    details: str | None
    image_urls: list[str]


def build_listing_presentation(
    listing: Listing,
    max_images: int,
) -> ListingPresentation:
    """Build a beautiful, Telegram-safe presentation for one listing."""
    image_urls = [image.gallery_url for image in listing.images[:max_images]]
    caption_limit = TELEGRAM_CAPTION_LIMIT if image_urls else TELEGRAM_MESSAGE_LIMIT
    caption = listing_message_text(listing, caption_limit)

    if image_urls:
        return ListingPresentation(
            caption=caption,
            details=None,
            image_urls=image_urls,
        )

    return ListingPresentation(
        caption=caption,
        details=None,
        image_urls=[],
    )


def listing_message_text(listing: Listing, limit: int) -> str:
    """Build listing text while trimming only description when space is tight."""
    header = listing_header(listing)
    facts = listing_facts(listing)
    description = full_description(listing)
    url = listing_url(listing)

    fixed_parts = [header]
    if facts:
        fixed_parts.append(facts)
    fixed_parts.append(url)

    if not description:
        return trim_for_telegram("\n\n".join(fixed_parts), limit)

    separator = "\n\n"
    description_prefix = "📝 <b>Описание:</b>\n"
    fixed_length = len(separator.join(fixed_parts))
    description_overhead = len(separator) * 2 + len(description_prefix)
    description_limit = limit - fixed_length - description_overhead
    if description_limit <= len(TELEGRAM_TRIM_SUFFIX):
        return trim_for_telegram("\n\n".join(fixed_parts), limit)

    description_block = (
        description_prefix + trim_escaped_text(description, description_limit)
    )
    text_parts = fixed_parts[:-1] + [description_block, url]
    return trim_for_telegram("\n\n".join(text_parts), limit)


def listing_header(listing: Listing) -> str:
    """Build the first line of a listing notification."""
    # Show both currencies when available (USD and BYN). Keep fallback text.
    label_parts: list[str] = []
    if listing.price_usd is not None:
        label_parts.append(f"{listing.price_usd:g} $")
    if listing.price_byn is not None:
        label_parts.append(f"{listing.price_byn:g} BYN")
    label = " / ".join(label_parts) if label_parts else "Договорная"
    return f"🆕 <b>{escape(label)}</b>\n{escape(listing.title)}"


def listing_facts(listing: Listing) -> str:
    """Build a compact facts block with location and apartment parameters."""
    source = SOURCE_LABELS.get(listing.source, listing.source)
    rows = [f"🌐 <b>Источник:</b> {escape(source)}"]
    specs = listing_specs(listing)
    if specs:
        rows.append(f"🏡 <b>Параметры:</b> {escape(specs)}")
    if listing.short_location:
        rows.append(f"📍 <b>Адрес:</b> {escape(listing.short_location)}")
    if listing.published_at:
        published = format_published_at(listing.published_at)
        rows.append(f"🕒 <b>Опубликовано:</b> {escape(published)}")
    return "\n".join(rows)


def listing_specs(listing: Listing) -> str:
    """Build a room/area/floor summary for one listing."""
    parts = []
    if listing.rooms:
        parts.append(f"{listing.rooms} комн.")
    if listing.area_m2:
        parts.append(f"{listing.area_m2:g} м2")
    if listing.floor:
        floor = f"этаж {listing.floor}"
        if listing.total_floors:
            floor = f"{floor} из {listing.total_floors}"
        parts.append(floor)
    return ", ".join(parts)


def full_description(listing: Listing) -> str | None:
    """Return the full listing description prepared for display."""
    if not listing.description:
        return None
    return listing.description.strip()


def description_message(description: str) -> str:
    """Format full description as a separate readable block."""
    return f"📝 <b>Описание:</b>\n{escape(description)}"


def listing_url(listing: Listing) -> str:
    """Format the public listing URL for Telegram messages."""
    return f"🔗 <b>Объявление:</b> {escape(listing.url)}"


def format_published_at(value: datetime) -> str:
    """Format listing publication time in the bot display timezone."""
    try:
        timezone = ZoneInfo(settings.bot_display_timezone)
    except ZoneInfoNotFoundError:
        timezone = ZoneInfo("Europe/Minsk")
    if value.tzinfo is None:
        return value.strftime("%d.%m.%Y %H:%M")
    return value.astimezone(timezone).strftime("%d.%m.%Y %H:%M")


def trim_for_telegram(text: str, limit: int) -> str:
    """Trim text to a Telegram API limit without cutting too aggressively."""
    if len(text) <= limit:
        return text
    return text[: limit - len(TELEGRAM_TRIM_SUFFIX)].rstrip() + TELEGRAM_TRIM_SUFFIX


def trim_escaped_text(text: str, limit: int) -> str:
    """Escape text and trim without cutting HTML entities in the middle."""
    escaped = escape(text)
    if len(escaped) <= limit:
        return escaped

    parts: list[str] = []
    length = 0
    content_limit = limit - len(TELEGRAM_TRIM_SUFFIX)
    for character in text:
        escaped_character = escape(character)
        if length + len(escaped_character) > content_limit:
            break
        parts.append(escaped_character)
        length += len(escaped_character)
    return "".join(parts).rstrip() + TELEGRAM_TRIM_SUFFIX
