import asyncio
import logging
from datetime import UTC, datetime
from types import SimpleNamespace

from apartmentfinder.application.filtering import listing_matches_search_filters
from apartmentfinder.application.monitoring import listings_after_watch_start
from apartmentfinder.domain.models import Listing, ListingImage, SearchRequest
from apartmentfinder.infrastructure.persistence.storage import UserProfile
from apartmentfinder.interfaces.telegram import bot as telegram_bot
from apartmentfinder.interfaces.telegram.bot import (
    enable_subscription_watch,
    history_keyboard,
    listing_history_url,
    listing_navigation_keyboard,
    notify_profile,
    parse_keywords,
    parse_price_range_text,
    send_listing,
    send_old_listing,
)


class FakeCallbackMessage:
    def __init__(self) -> None:
        self.photos: list[dict[str, object]] = []
        self.media_groups: list[list[object]] = []
        self.messages: list[dict[str, object]] = []
        self.edited_texts: list[dict[str, object]] = []

    async def answer(self, *args: object, **kwargs: object) -> None:
        self.messages.append({"args": args, "kwargs": kwargs})
        return None

    async def edit_text(self, *args: object, **kwargs: object) -> None:
        self.edited_texts.append({"args": args, "kwargs": kwargs})
        return None

    async def answer_photo(self, *args: object, **kwargs: object) -> None:
        self.photos.append({"args": args, "kwargs": kwargs})
        return None

    async def answer_media_group(self, media: list[object]) -> None:
        self.media_groups.append(media)
        return None


class FakeCallback:
    message = FakeCallbackMessage()


class FakeBot:
    def __init__(self) -> None:
        self.photos: list[dict[str, object]] = []
        self.media_groups: list[list[object]] = []
        self.messages: list[dict[str, object]] = []

    async def send_photo(self, *args: object, **kwargs: object) -> None:
        self.photos.append({"args": args, "kwargs": kwargs})

    async def send_media_group(self, chat_id: int, media: list[object]) -> None:
        self.media_groups.append(media)

    async def send_message(self, *args: object, **kwargs: object) -> None:
        self.messages.append({"args": args, "kwargs": kwargs})


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


def test_listing_history_url_uses_public_realt_object_paths() -> None:
    room_request = SearchRequest(property_type="room")
    flat_request = SearchRequest(property_type="apartment")

    assert listing_history_url("realt", 4146299, room_request) == (
        "https://realt.by/rent-rooms-for-long/object/4146299/"
    )
    assert listing_history_url("realt", 4137638, flat_request) == (
        "https://realt.by/rent-flat-for-long/object/4137638/"
    )
    assert listing_history_url("kufar", 123, flat_request) == (
        "https://re.kufar.by/vi/123"
    )


def test_listing_navigation_keyboard_includes_seller_ban_when_known() -> None:
    listing = Listing(
        ad_id=123,
        title="Квартира",
        url="https://example.test/123",
        source="realt",
        seller_name="Агентство",
    )

    keyboard = listing_navigation_keyboard(listing)
    buttons = [button for row in keyboard.inline_keyboard for button in row]

    assert any(button.callback_data == "fav:add:realt:123" for button in buttons)
    assert any(button.callback_data == "ban:realt:123" for button in buttons)
    assert any(button.callback_data == "menu:main" for button in buttons)


def test_listing_navigation_keyboard_skips_seller_ban_without_name() -> None:
    listing = Listing(
        ad_id=123,
        title="Квартира",
        url="https://example.test/123",
        source="realt",
    )

    keyboard = listing_navigation_keyboard(listing)
    buttons = [button for row in keyboard.inline_keyboard for button in row]

    assert any(button.callback_data == "fav:add:realt:123" for button in buttons)
    assert not any(button.callback_data == "ban:realt:123" for button in buttons)
    assert any(button.callback_data == "menu:main" for button in buttons)


def test_send_listing_keeps_multiple_images_in_one_album() -> None:
    listing = Listing(
        ad_id=123,
        title="Квартира",
        url="https://example.test/123",
        images=[
            ListingImage(gallery_url="https://img.test/1.jpg"),
            ListingImage(gallery_url="https://img.test/2.jpg"),
            ListingImage(gallery_url="https://img.test/3.jpg"),
        ],
    )
    bot = FakeBot()

    asyncio.run(send_listing(bot, 123, listing))

    assert bot.photos == []
    assert len(bot.media_groups) == 1
    assert [item.media for item in bot.media_groups[0]] == [
        "https://img.test/1.jpg",
        "https://img.test/2.jpg",
        "https://img.test/3.jpg",
    ]
    assert bot.media_groups[0][0].caption
    assert bot.media_groups[0][1].caption is None
    assert bot.media_groups[0][2].caption is None
    assert bot.messages[0]["args"][1] == "Действия с объявлением:"


def test_send_old_listing_includes_image_when_history_snapshot_has_photo(
    monkeypatch,
) -> None:
    listing = Listing(
        ad_id=123,
        title="История",
        url="https://example.test/123",
        images=[ListingImage(gallery_url="https://img.test/1.jpg")],
    )
    fake_storage = SimpleNamespace(
        listing_history_count_for_subscription=lambda _id: 1,
        history_listing_for_subscription=lambda _id, _index: listing,
    )
    monkeypatch.setattr(telegram_bot, "storage", fake_storage)
    message = FakeCallbackMessage()

    asyncio.run(send_old_listing(message, UserProfile(chat_id=123, id=7), 0))

    assert message.edited_texts == []
    assert message.photos[0]["args"][0] == "https://img.test/1.jpg"
    assert message.photos[0]["kwargs"]["caption"].startswith(
        "🕘 <b>Старое объявление 1/1</b>"
    )


def test_enable_subscription_watch_does_not_save_current_listings_to_history(
    monkeypatch,
) -> None:
    listings = [
        Listing(ad_id=1, title="Текущая выдача", url="https://example.test/1")
    ]
    saved_history: list[list[Listing]] = []
    marked_seen: list[list[Listing]] = []

    async def fake_run_profile_check(
        profile: UserProfile,
        operation,
        *,
        skip_if_running: bool = False,
    ) -> list[Listing]:
        return await operation()

    async def fake_fetch_listings(profile: UserProfile) -> list[Listing]:
        return listings

    fake_storage = SimpleNamespace(update_subscription=lambda profile: None)
    monkeypatch.setattr(telegram_bot, "storage", fake_storage)
    monkeypatch.setattr(telegram_bot, "run_profile_check", fake_run_profile_check)
    monkeypatch.setattr(telegram_bot, "fetch_listings", fake_fetch_listings)
    monkeypatch.setattr(
        telegram_bot,
        "save_matching_listing_history",
        lambda profile, items: saved_history.append(items),
    )
    monkeypatch.setattr(
        telegram_bot,
        "mark_subscription_seen",
        lambda profile, items: marked_seen.append(items),
    )

    profile = UserProfile(chat_id=123, id=1)

    asyncio.run(enable_subscription_watch(FakeCallback(), profile))

    assert profile.enabled is True
    assert profile.watch_started_at is not None
    assert marked_seen == [listings]
    assert saved_history == []


def test_notify_profile_saves_only_unnotified_new_listings_to_history(
    monkeypatch,
) -> None:
    started_at = datetime(2026, 5, 7, 12, 0, tzinfo=UTC)
    listings = [
        Listing(
            ad_id=ad_id,
            title=f"Новое {ad_id}",
            url=f"https://example.test/{ad_id}",
            published_at=datetime(2026, 5, 7, 12, ad_id, tzinfo=UTC),
        )
        for ad_id in range(1, 5)
    ]
    saved_history: list[list[Listing]] = []
    sent_ids: list[int] = []
    marked_seen: list[list[Listing]] = []

    async def fake_run_profile_check(
        profile: UserProfile,
        operation,
        *,
        skip_if_running: bool = False,
    ) -> list[Listing]:
        return await operation()

    async def fake_fetch_listings(profile: UserProfile) -> list[Listing]:
        return listings

    async def fake_fetch_listing_details(items: list[Listing]) -> list[Listing]:
        return items

    async def fake_send_listing(bot: object, chat_id: int, listing: Listing) -> None:
        sent_ids.append(listing.ad_id)

    fake_storage = SimpleNamespace(
        is_seller_banned=lambda chat_id, source, seller_name: False,
        log_notification_for_subscription=lambda *args, **kwargs: None,
        update_subscription=lambda profile: None,
    )
    monkeypatch.setattr(telegram_bot, "storage", fake_storage)
    monkeypatch.setattr(
        telegram_bot,
        "settings",
        SimpleNamespace(bot_max_notifications_per_check=2),
    )
    monkeypatch.setattr(telegram_bot, "run_profile_check", fake_run_profile_check)
    monkeypatch.setattr(telegram_bot, "fetch_listings", fake_fetch_listings)
    monkeypatch.setattr(
        telegram_bot,
        "fetch_listing_details",
        fake_fetch_listing_details,
    )
    monkeypatch.setattr(telegram_bot, "send_listing", fake_send_listing)
    monkeypatch.setattr(
        telegram_bot,
        "unseen_items_for_subscription",
        lambda profile, keys: keys,
    )
    monkeypatch.setattr(
        telegram_bot,
        "save_matching_listing_history",
        lambda profile, items: saved_history.append(items),
    )
    monkeypatch.setattr(
        telegram_bot,
        "mark_subscription_seen",
        lambda profile, items: marked_seen.append(items),
    )

    profile = UserProfile(chat_id=123, id=1, watch_started_at=started_at)

    asyncio.run(notify_profile(object(), profile))

    assert sent_ids == [2, 1]
    assert [[listing.ad_id for listing in items] for items in saved_history] == [[3, 4]]
    assert marked_seen == [listings]


def test_history_keyboard_opens_old_listing_view_when_history_exists(
    monkeypatch,
) -> None:
    fake_storage = SimpleNamespace(listing_history_count_for_subscription=lambda _id: 1)
    monkeypatch.setattr(telegram_bot, "storage", fake_storage)

    keyboard = history_keyboard(UserProfile(chat_id=123, id=7), 0)
    buttons = [button for row in keyboard.inline_keyboard for button in row]

    assert any(button.callback_data == "s:7:old:0" for button in buttons)
