from datetime import UTC, datetime

from kufarpars.bot_storage import UserProfile
from kufarpars.models import Listing
from kufarpars.telegram_bot import build_preview_listing, listings_after_watch_start


def test_listings_after_watch_start_keeps_only_newer_items() -> None:
    profile = UserProfile(
        chat_id=123,
        watch_started_at=datetime(2026, 5, 7, 12, 0, tzinfo=UTC),
    )
    old_listing = Listing(
        ad_id=1,
        title="Старое",
        url="https://example.test/1",
        published_at=datetime(2026, 5, 7, 11, 59, tzinfo=UTC),
    )
    new_listing = Listing(
        ad_id=2,
        title="Новое",
        url="https://example.test/2",
        published_at=datetime(2026, 5, 7, 12, 1, tzinfo=UTC),
    )
    undated_listing = Listing(
        ad_id=3,
        title="Без даты",
        url="https://example.test/3",
    )

    assert listings_after_watch_start(
        profile,
        [old_listing, new_listing, undated_listing],
    ) == [new_listing]


def test_listings_after_watch_start_returns_empty_without_start_time() -> None:
    profile = UserProfile(chat_id=123, watch_started_at=None)
    listing = Listing(
        ad_id=1,
        title="Новое",
        url="https://example.test/1",
        published_at=datetime(2026, 5, 7, 12, 1, tzinfo=UTC),
    )

    assert listings_after_watch_start(profile, [listing]) == []


def test_listings_after_watch_start_ignores_equal_timestamp() -> None:
    started_at = datetime(2026, 5, 7, 12, 0, tzinfo=UTC)
    profile = UserProfile(chat_id=123, watch_started_at=started_at)
    listing = Listing(
        ad_id=1,
        title="На границе",
        url="https://example.test/1",
        published_at=started_at,
    )

    assert listings_after_watch_start(profile, [listing]) == []


def test_build_preview_listing_has_stable_display_data() -> None:
    listing = build_preview_listing()

    assert listing.ad_id == 0
    assert listing.price_usd == 180
    assert listing.description
    assert listing.images
    assert "preview" in listing.url
