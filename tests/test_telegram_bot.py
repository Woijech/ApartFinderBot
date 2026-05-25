import logging
from datetime import UTC, datetime

from apartmentfinder.application.filtering import listing_matches_search_filters
from apartmentfinder.application.monitoring import listings_after_watch_start
from apartmentfinder.domain.models import Listing, SearchRequest
from apartmentfinder.infrastructure.persistence.storage import UserProfile
from apartmentfinder.interfaces.telegram.bot import (
    parse_keywords,
    parse_price_range_text,
)


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
    ) == [new_listing, undated_listing]


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


def test_listing_matches_search_filters_uses_keywords_and_exclusions() -> None:
    listing = Listing(
        ad_id=1,
        title="Сдам комнату без хозяев",
        url="https://example.test/1",
        address="Центральный район",
        metro=["Немига"],
        description="Можно на длительный срок.",
    )
    request = SearchRequest(
        district="Центральный",
        metro="Немига",
        include_keywords=["без хозяев"],
        exclude_keywords=["койко-место"],
    )

    assert listing_matches_search_filters(listing, request) is True

    blocked = SearchRequest(exclude_keywords=["длительный срок"])

    assert listing_matches_search_filters(listing, blocked) is False


def test_listing_matches_search_filters_checks_usd_price_range() -> None:
    listing = Listing(
        ad_id=1,
        title="Сдам комнату",
        url="https://example.test/1",
        price_usd=180,
    )

    assert listing_matches_search_filters(
        listing,
        SearchRequest(min_price=150, max_price=250),
    )
    assert not listing_matches_search_filters(
        listing,
        SearchRequest(min_price=200, max_price=300),
    )


def test_listing_matches_search_filters_excludes_unknown_price_when_range_set() -> None:
    listing = Listing(
        ad_id=1,
        title="Сдам комнату",
        url="https://example.test/1",
        price_byn=500,
    )

    assert not listing_matches_search_filters(listing, SearchRequest(max_price=250))


def test_listing_matches_search_filters_logs_rejection_reason(caplog) -> None:
    listing = Listing(
        ad_id=1,
        title="Сдам комнату",
        url="https://example.test/1",
        price_usd=180,
    )

    with caplog.at_level(logging.DEBUG, logger="apartmentfinder.application"):
        assert not listing_matches_search_filters(
            listing,
            SearchRequest(min_price=200),
        )

    assert "listing_filtered_out source=kufar ad_id=1 reason=price" in caplog.text
    assert "min_price=200" in caplog.text


def test_parse_keywords_accepts_commas_and_lines() -> None:
    assert parse_keywords("без хозяев, метро\nдлительно") == [
        "без хозяев",
        "метро",
        "длительно",
    ]


def test_parse_price_range_text_accepts_common_forms() -> None:
    assert parse_price_range_text("150-250") == (150, 250)
    assert parse_price_range_text("до 300") == (None, 300)
    assert parse_price_range_text("500") == (None, 500)
