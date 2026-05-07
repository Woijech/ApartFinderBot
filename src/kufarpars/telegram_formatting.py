"""Telegram presentation helpers for parsed listings.

This module owns message text, captions, and image selection. Keeping formatting
outside handlers makes the bot easier to extend with new categories and keeps
Telegram-specific limits in one place.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape

from kufarpars.models import Listing

TELEGRAM_MESSAGE_LIMIT = 4096
TELEGRAM_CAPTION_LIMIT = 1024


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
    header = listing_header(listing)
    facts = listing_facts(listing)
    description = full_description(listing)
    url = escape(listing.url)
    image_urls = [image.gallery_url for image in listing.images[:max_images]]

    if image_urls:
        caption = trim_for_telegram("\n".join([header, facts, url]), 900)
        details = (
            trim_for_telegram(description_message(description), TELEGRAM_MESSAGE_LIMIT)
            if description
            else None
        )
        return ListingPresentation(
            caption=caption,
            details=details,
            image_urls=image_urls,
        )

    text_parts = [header, facts]
    if description:
        text_parts.append(description_message(description))
    text_parts.append(url)
    return ListingPresentation(
        caption=trim_for_telegram("\n\n".join(text_parts), TELEGRAM_MESSAGE_LIMIT),
        details=None,
        image_urls=[],
    )


def listing_header(listing: Listing) -> str:
    """Build the first line of a listing notification."""
    return f"<b>{escape(listing.price_label)}</b> | {escape(listing.title)}"


def listing_facts(listing: Listing) -> str:
    """Build a compact facts block with location and apartment parameters."""
    rows = []
    specs = listing_specs(listing)
    if specs:
        rows.append(f"<b>Параметры:</b> {escape(specs)}")
    if listing.short_location:
        rows.append(f"<b>Адрес:</b> {escape(listing.short_location)}")
    if listing.seller_name:
        rows.append(f"<b>Контакт:</b> {escape(listing.seller_name)}")
    if listing.published_at:
        published = listing.published_at.strftime("%d.%m.%Y %H:%M")
        rows.append(f"<b>Опубликовано:</b> {escape(published)}")
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
    return f"<b>Описание:</b>\n{escape(description)}"


def trim_for_telegram(text: str, limit: int) -> str:
    """Trim text to a Telegram API limit without cutting too aggressively."""
    if len(text) <= limit:
        return text
    suffix = "\n..."
    return text[: limit - len(suffix)].rstrip() + suffix
