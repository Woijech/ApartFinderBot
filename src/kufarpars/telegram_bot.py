"""Aiogram bot entry point and interaction handlers.

The bot keeps user interaction intentionally thin: it renders buttons, stores
profile choices, fetches listings, enriches only the listings that will be sent,
and delegates display formatting to ``telegram_formatting``. Add new searchable
targets in ``search_catalog`` and new presentation rules in
``telegram_formatting``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime
from html import escape
from sys import exit
from typing import Any, TypeVar

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
    TelegramObject,
)
from sqlalchemy.exc import SQLAlchemyError

from kufarpars.bot_storage import BotStorage, UserProfile
from kufarpars.client import KufarClient, KufarNetworkError, SearchRequest
from kufarpars.config import settings
from kufarpars.models import Listing, ListingImage
from kufarpars.search_catalog import (
    DISTRICT_OPTIONS,
    METRO_OPTIONS,
    PRICE_RANGES,
    ROOM_OPTIONS,
    SEARCH_TARGETS,
    default_request,
    district_option_by_code,
    metro_option_by_code,
    price_range_by_code,
    room_option_by_code,
    target_by_code,
    target_for_request,
)
from kufarpars.telegram_formatting import (
    ListingPresentation,
    build_listing_presentation,
)

router = Router()
logger = logging.getLogger(__name__)
T = TypeVar("T")
profile_locks: dict[int, asyncio.Lock] = {}
pending_text_inputs: dict[int, tuple[int, str]] = {}

class LazyBotStorage:
    """Initialize PostgreSQL storage only when bot code first needs it."""

    def __init__(self) -> None:
        """Create an empty lazy storage holder."""
        self._instance: BotStorage | None = None

    def get_instance(self) -> BotStorage:
        """Return the real storage repository, creating it on first use."""
        if self._instance is None:
            self._instance = BotStorage(
                settings.database_url,
                seen_ttl_days=settings.seen_ttl_days,
                max_seen_per_chat=settings.max_seen_per_chat,
                create_schema=False,
            )
        return self._instance

    def close(self) -> None:
        """Close storage if it has been initialized."""
        if self._instance is not None:
            self._instance.close()

    def __getattr__(self, name: str) -> Any:
        """Delegate storage method calls to the real repository."""
        return getattr(self.get_instance(), name)


storage = LazyBotStorage()

CALLBACK_MAIN = "menu:main"
CALLBACK_SEARCHES = "menu:searches"
CALLBACK_NEW_SEARCH = "subscription:new"
CALLBACK_HELP = "menu:help"
CALLBACK_FILTERS = "menu:filters"
CALLBACK_TYPE = "filter:type"
CALLBACK_PRICE = "filter:price"
CALLBACK_WATCH_ON = "action:watch_on"
CALLBACK_WATCH_OFF = "action:watch_off"
CALLBACK_SETTINGS = "action:settings"


class AccessMiddleware(BaseMiddleware):
    """Block Telegram chats that are not explicitly allowed."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        """Allow the update only when chat id passes configured allowlist."""
        chat_id = event_chat_id(event)
        if chat_id is None or is_chat_allowed(chat_id):
            return await handler(event, data)
        if isinstance(event, Message):
            await event.answer("🔒 Бот приватный. Этот чат не разрешён.")
        elif isinstance(event, CallbackQuery):
            await event.answer("Бот приватный", show_alert=True)
        return None


def event_chat_id(event: TelegramObject) -> int | None:
    """Extract chat id from supported Telegram update event objects."""
    if isinstance(event, Message):
        return event.chat.id
    if isinstance(event, CallbackQuery) and event.message is not None:
        return event.message.chat.id
    return None


def is_chat_allowed(chat_id: int) -> bool:
    """Return whether a chat is allowed to use the bot."""
    return not settings.allowed_chat_ids or chat_id in settings.allowed_chat_ids


@router.message(Command("start", "menu"))
async def start(message: Message) -> None:
    """Open the main menu and create a default profile if needed."""
    ensure_default_profile(message.chat.id)
    await message.answer(
        main_menu_text(message.chat.id),
        reply_markup=main_menu_keyboard(),
        disable_web_page_preview=True,
    )


@router.message(Command("settings"))
async def show_settings(message: Message) -> None:
    """Show current settings for users who prefer a command."""
    ensure_default_profile(message.chat.id)
    await message.answer(
        subscriptions_text(message.chat.id),
        reply_markup=searches_keyboard(message.chat.id),
        disable_web_page_preview=True,
    )


@router.message(Command("status"))
async def show_status(message: Message) -> None:
    """Show monitoring status for the current chat."""
    ensure_default_profile(message.chat.id)
    await message.answer(
        status_text(message.chat.id),
        reply_markup=main_menu_keyboard(),
        disable_web_page_preview=True,
    )


@router.message(Command("preview"))
async def preview_listing(message: Message, bot: Bot) -> None:
    """Send a local fake listing so developers can inspect Telegram formatting."""
    if not settings.bot_enable_preview:
        await message.answer(
            "🧪 Preview-режим выключен.\n\n"
            "Для локального теста добавь в .env:\n"
            "<code>KUFARPARS_BOT_ENABLE_PREVIEW=true</code>"
        )
        return
    await message.answer("🧪 Отправляю пример уведомления без запроса к Kufar.")
    await send_listing(bot, message.chat.id, build_preview_listing())


@router.message(F.text)
async def text_input(message: Message) -> None:
    """Handle pending free-text filter inputs."""
    pending = pending_text_inputs.pop(message.chat.id, None)
    if pending is None:
        return
    subscription_id, action = pending
    subscription = storage.get_subscription(message.chat.id, subscription_id)
    try:
        subscription.request = apply_text_input(
            subscription.request,
            action,
            message.text or "",
        )
    except ValueError as error:
        await message.answer(
            f"Не понял значение: {escape(str(error))}",
            reply_markup=subscription_filter_keyboard(subscription),
        )
        return
    restart_subscription_watch(subscription)
    storage.reset_seen_for_subscription(subscription.id)
    storage.update_subscription(subscription)
    await message.answer(
        "✅ Фильтр сохранён.\n\n" + subscription_text(subscription),
        reply_markup=subscription_keyboard(subscription),
        disable_web_page_preview=True,
    )


@router.callback_query(F.data == CALLBACK_MAIN)
async def main_menu(callback: CallbackQuery) -> None:
    """Render the main menu after a user presses back/home."""
    ensure_default_profile(callback.message.chat.id)
    await callback.message.edit_text(
        main_menu_text(callback.message.chat.id),
        reply_markup=main_menu_keyboard(),
        disable_web_page_preview=True,
    )
    await callback.answer()


@router.callback_query(F.data == CALLBACK_SEARCHES)
async def searches_menu(callback: CallbackQuery) -> None:
    """Render saved searches for the current chat."""
    ensure_default_profile(callback.message.chat.id)
    await callback.message.edit_text(
        subscriptions_text(callback.message.chat.id),
        reply_markup=searches_keyboard(callback.message.chat.id),
        disable_web_page_preview=True,
    )
    await callback.answer()


@router.callback_query(F.data == CALLBACK_NEW_SEARCH)
async def create_search(callback: CallbackQuery) -> None:
    """Create a new saved search with default filters."""
    subscriptions = storage.list_subscriptions(callback.message.chat.id)
    title = f"Поиск {len(subscriptions) + 1}"
    subscription = storage.create_subscription(
        callback.message.chat.id,
        title,
        default_request(),
    )
    await callback.message.edit_text(
        "➕ <b>Новый поиск создан</b>\n\n" + subscription_text(subscription),
        reply_markup=subscription_keyboard(subscription),
        disable_web_page_preview=True,
    )
    await callback.answer("Поиск создан")


@router.callback_query(F.data == CALLBACK_HELP)
async def help_menu(callback: CallbackQuery) -> None:
    """Show a short help screen."""
    await callback.message.edit_text(
        "❔ <b>Как пользоваться</b>\n\n"
        "Создай один или несколько поисков, настрой фильтры и включи слежение. "
        "Бот запомнит текущую выдачу и дальше будет присылать только новые "
        "объявления, опубликованные после включения слежения.\n\n"
        "Ключевые слова должны быть в описании или заголовке. Исключающие слова "
        "работают наоборот: если слово найдено, объявление не будет отправлено.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data=CALLBACK_MAIN)]
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("s:"))
async def subscription_callback(callback: CallbackQuery) -> None:
    """Route subscription-specific callbacks."""
    subscription_id, action, value = parse_subscription_callback(callback.data)
    subscription = storage.get_subscription(callback.message.chat.id, subscription_id)

    if action == "open":
        await callback.message.edit_text(
            subscription_text(subscription),
            reply_markup=subscription_keyboard(subscription),
            disable_web_page_preview=True,
        )
        await callback.answer()
        return
    if action == "filters":
        await callback.message.edit_text(
            subscription_filters_text(subscription),
            reply_markup=subscription_filter_keyboard(subscription),
            disable_web_page_preview=True,
        )
        await callback.answer()
        return
    if action == "delete":
        storage.delete_subscription(callback.message.chat.id, subscription_id)
        await callback.message.edit_text(
            subscriptions_text(callback.message.chat.id),
            reply_markup=searches_keyboard(callback.message.chat.id),
            disable_web_page_preview=True,
        )
        await callback.answer("Поиск удалён")
        return
    if action == "watch_on":
        await enable_subscription_watch(callback, subscription)
        return
    if action == "watch_off":
        subscription.enabled = False
        subscription.watch_started_at = None
        storage.update_subscription(subscription)
        await callback.message.edit_text(
            "⏸ <b>Слежение выключено</b>\n\n" + subscription_text(subscription),
            reply_markup=subscription_keyboard(subscription),
            disable_web_page_preview=True,
        )
        await callback.answer()
        return
    if action in {"type", "price", "rooms", "district", "metro"}:
        await callback.message.edit_text(
            filter_option_text(action),
            reply_markup=filter_option_keyboard(subscription, action),
            disable_web_page_preview=True,
        )
        await callback.answer()
        return
    if action in {"include", "exclude", "custom_price"}:
        pending_text_inputs[callback.message.chat.id] = (subscription.id, action)
        await callback.message.edit_text(
            text_input_prompt(action),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="⬅️ Назад",
                            callback_data=f"s:{subscription.id}:filters",
                        )
                    ]
                ]
            ),
        )
        await callback.answer()
        return
    if action.startswith("set_"):
        apply_option_value(subscription, action, value)
        restart_subscription_watch(subscription)
        storage.reset_seen_for_subscription(subscription.id)
        storage.update_subscription(subscription)
        await callback.message.edit_text(
            subscription_filters_text(subscription),
            reply_markup=subscription_filter_keyboard(subscription),
            disable_web_page_preview=True,
        )
        await callback.answer("Фильтр сохранён")
        return
    raise ValueError(f"Unknown subscription action: {action}")


@router.callback_query(F.data == CALLBACK_FILTERS)
async def filters_menu(callback: CallbackQuery) -> None:
    """Render the supported filter menu."""
    profile = ensure_default_profile(callback.message.chat.id)
    await callback.message.edit_text(
        filters_menu_text(profile),
        reply_markup=filters_keyboard(),
        disable_web_page_preview=True,
    )
    await callback.answer()


@router.callback_query(F.data == CALLBACK_TYPE)
async def property_type_menu(callback: CallbackQuery) -> None:
    """Render buttons for selecting a search target."""
    profile = ensure_default_profile(callback.message.chat.id)
    await callback.message.edit_text(
        "Выбери тип жилья:",
        reply_markup=property_type_keyboard(profile),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("set:type:"))
async def set_property_type(callback: CallbackQuery) -> None:
    """Persist the selected search target and reset seen listing ids."""
    target_code = callback.data.rsplit(":", maxsplit=1)[-1]
    target = target_by_code(target_code)
    profile = ensure_default_profile(callback.message.chat.id)
    profile.request = replace_request(profile.request, **target.request_patch)
    profile.watch_started_at = datetime.now(UTC) if profile.enabled else None
    storage.reset_seen(profile.chat_id)
    profile.seen_ids = []
    storage.update(profile)
    await callback.message.edit_text(
        filters_menu_text(profile),
        reply_markup=filters_keyboard(),
        disable_web_page_preview=True,
    )
    await callback.answer("Тип жилья сохранён")


@router.callback_query(F.data == CALLBACK_PRICE)
async def price_menu(callback: CallbackQuery) -> None:
    """Render price-range preset buttons."""
    profile = ensure_default_profile(callback.message.chat.id)
    await callback.message.edit_text(
        "Выбери диапазон цены:",
        reply_markup=price_keyboard(profile),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("set:price:"))
async def set_price(callback: CallbackQuery) -> None:
    """Persist the selected price range and reset seen listing ids."""
    code = callback.data.rsplit(":", maxsplit=1)[-1]
    price_range = price_range_by_code(code)
    profile = ensure_default_profile(callback.message.chat.id)
    profile.request = replace_request(
        profile.request,
        min_price=price_range.min_price,
        max_price=price_range.max_price,
    )
    profile.watch_started_at = datetime.now(UTC) if profile.enabled else None
    storage.reset_seen(profile.chat_id)
    profile.seen_ids = []
    storage.update(profile)
    await callback.message.edit_text(
        filters_menu_text(profile),
        reply_markup=filters_keyboard(),
        disable_web_page_preview=True,
    )
    await callback.answer("Цена сохранена")


@router.callback_query(F.data == CALLBACK_SETTINGS)
async def settings_callback(callback: CallbackQuery) -> None:
    """Show current settings from an inline button."""
    profile = ensure_default_profile(callback.message.chat.id)
    await callback.message.edit_text(
        settings_text(profile),
        reply_markup=main_menu_keyboard(profile),
        disable_web_page_preview=True,
    )
    await callback.answer()


@router.callback_query(F.data == CALLBACK_WATCH_ON)
async def watch_on_callback(callback: CallbackQuery) -> None:
    """Enable monitoring and remember current listings as already seen."""
    profile = ensure_default_profile(callback.message.chat.id)
    await callback.answer("Включаю слежение...")
    await enable_subscription_watch(callback, profile)


async def enable_subscription_watch(
    callback: CallbackQuery,
    profile: UserProfile,
) -> None:
    """Enable monitoring for one saved search and seed seen ids."""
    try:
        listings = await run_profile_check(profile, lambda: fetch_listings(profile))
    except KufarNetworkError:
        logging.warning("Kufar is temporarily unavailable before enabling watch")
        await callback.message.answer(
            "Не смог проверить Kufar перед включением слежения. "
            "Попробуй включить ещё раз чуть позже.",
            reply_markup=subscription_keyboard(profile),
        )
        return
    except ProfileCheckAlreadyRunning:
        await callback.message.answer(
            "Сейчас уже идёт проверка Kufar. "
            "Попробуй включить слежение через пару секунд.",
            reply_markup=subscription_keyboard(profile),
        )
        return
    profile.enabled = True
    profile.watch_started_at = datetime.now(UTC)
    mark_subscription_seen(profile, listings)
    storage.update_subscription(profile)
    await callback.message.edit_text(
        "✅ <b>Слежение включено</b>\n\n"
        "Текущую выдачу запомнил. Дальше буду присылать только новые объявления.\n\n"
        f"📌 Запомнено объявлений: <b>{len(profile.seen_ids)}</b>",
        reply_markup=subscription_keyboard(profile),
    )


@router.callback_query(F.data == CALLBACK_WATCH_OFF)
async def watch_off_callback(callback: CallbackQuery) -> None:
    """Disable monitoring for the current chat."""
    profile = ensure_default_profile(callback.message.chat.id)
    profile.enabled = False
    profile.watch_started_at = None
    storage.update(profile)
    await callback.message.edit_text(
        "⏸ <b>Слежение выключено</b>\n\n"
        "Фильтры сохранены, можно включить уведомления обратно в любой момент.",
        reply_markup=main_menu_keyboard(profile),
    )
    await callback.answer()


async def notifier_loop(bot: Bot, stop_event: asyncio.Event) -> None:
    """Continuously poll Kufar and notify enabled chats about new listings."""
    await sleep_or_stop(stop_event, settings.bot_initial_poll_delay_seconds)
    while not stop_event.is_set():
        for profile in storage.all_enabled():
            if stop_event.is_set():
                break
            await notify_profile(bot, profile)
        await sleep_or_stop(stop_event, settings.bot_poll_interval_seconds)


async def sleep_or_stop(stop_event: asyncio.Event, delay_seconds: float) -> None:
    """Sleep until the next polling cycle or return early during shutdown."""
    if delay_seconds <= 0:
        return
    with suppress(asyncio.TimeoutError):
        await asyncio.wait_for(stop_event.wait(), timeout=delay_seconds)


async def notify_profile(bot: Bot, profile: UserProfile) -> None:
    """Check one profile and send only listings that were not seen before."""
    started_at = datetime.now(UTC)
    try:
        listings = await run_profile_check(
            profile,
            lambda: fetch_listings(profile),
            skip_if_running=True,
        )
    except ProfileCheckAlreadyRunning:
        return
    except KufarNetworkError:
        logger.warning(
            "kufar_check_failed chat_id=%s subscription_id=%s",
            profile.chat_id,
            profile.id,
        )
        return

    if profile.watch_started_at is None:
        profile.watch_started_at = datetime.now(UTC)
        mark_subscription_seen(profile, listings)
        storage.update_subscription(profile)
        return

    fresh_listings = [
        listing
        for listing in listings_after_watch_start(profile, listings)
        if listing_matches_search_filters(listing, profile.request)
    ]
    unseen_ids = set(
        unseen_ids_for_subscription(
            profile,
            [listing.ad_id for listing in fresh_listings],
        )
    )
    new_listings = [
        listing for listing in fresh_listings if listing.ad_id in unseen_ids
    ]
    if not new_listings:
        mark_subscription_seen(profile, listings)
        logger.info(
            "kufar_check_no_new chat_id=%s subscription_id=%s total=%s duration_ms=%s",
            profile.chat_id,
            profile.id,
            len(listings),
            elapsed_ms(started_at),
        )
        return

    notification_limit = max(0, settings.bot_max_notifications_per_check)
    listings_to_send = new_listings[:notification_limit]

    enriched = await fetch_listing_details(list(reversed(listings_to_send)))
    matched_enriched = [
        listing
        for listing in enriched
        if listing_matches_search_filters(listing, profile.request)
    ]
    for listing in matched_enriched:
        await send_listing(bot, profile.chat_id, listing)
        if profile.id is not None:
            storage.log_notification_for_subscription(profile.id, listing.ad_id, "sent")
        else:
            storage.log_notification(profile.chat_id, listing.ad_id, "sent")
    mark_subscription_seen(profile, listings)
    storage.update_subscription(profile)
    logger.info(
        "kufar_notifications_sent chat_id=%s subscription_id=%s total=%s sent=%s "
        "duration_ms=%s",
        profile.chat_id,
        profile.id,
        len(listings),
        len(matched_enriched),
        elapsed_ms(started_at),
    )


async def fetch_listings(profile: UserProfile) -> list[Listing]:
    """Fetch matching search-page listings without loading detail pages."""

    def fetch() -> list[Listing]:
        """Run the synchronous Kufar search client for one profile."""
        with bot_kufar_client() as client:
            return list(
                client.search_pages(
                    profile.request,
                    max_pages=settings.bot_max_pages,
                    delay_seconds=settings.bot_page_delay_seconds,
                )
            )

    return await asyncio.to_thread(fetch)


def listings_after_watch_start(
    profile: UserProfile,
    listings: list[Listing],
) -> list[Listing]:
    """Return only listings published after monitoring was enabled."""
    if profile.watch_started_at is None:
        return []
    return [
        listing
        for listing in listings
        if listing.published_at is not None
        and listing.published_at > profile.watch_started_at
    ]


def listing_matches_search_filters(listing: Listing, request: SearchRequest) -> bool:
    """Apply app-level filters that are not always represented in Kufar URL params."""
    haystack = " ".join(
        part
        for part in [
            listing.title,
            listing.description,
            listing.address,
            " ".join(listing.metro),
        ]
        if part
    ).casefold()
    if request.rooms is not None and listing.rooms:
        if str(request.rooms) != str(listing.rooms):
            return False
    if request.metro and request.metro.casefold() not in haystack:
        return False
    if request.district and request.district.casefold() not in haystack:
        return False
    if any(keyword.casefold() not in haystack for keyword in request.include_keywords):
        return False
    return not any(
        keyword.casefold() in haystack for keyword in request.exclude_keywords
    )


def unseen_ids_for_subscription(
    profile: UserProfile,
    ad_ids: list[int],
) -> list[int]:
    """Return unseen ids for a profile, preferring subscription-specific storage."""
    if profile.id is not None:
        return storage.unseen_ids_for_subscription(profile.id, ad_ids)
    return storage.unseen_ids(profile.chat_id, ad_ids)


def mark_subscription_seen(profile: UserProfile, listings: list[Listing]) -> None:
    """Mark current listings as seen for one saved search."""
    ad_ids = [listing.ad_id for listing in listings]
    if profile.id is not None:
        storage.mark_seen_for_subscription(profile.id, ad_ids)
        refreshed = storage.get_subscription(profile.chat_id, profile.id)
        profile.seen_ids = refreshed.seen_ids
        return
    storage.mark_seen(profile.chat_id, ad_ids)
    profile.seen_ids = storage.recent_seen_ids(profile.chat_id)


def elapsed_ms(started_at: datetime) -> int:
    """Return elapsed milliseconds from a UTC start timestamp."""
    return int((datetime.now(UTC) - started_at).total_seconds() * 1000)


async def fetch_listing_details(listings: list[Listing]) -> list[Listing]:
    """Load full descriptions and gallery URLs only for listings being sent."""

    def fetch() -> list[Listing]:
        """Fetch detail pages with one HTTP client for connection reuse."""
        enriched = []
        with bot_kufar_client() as client:
            for listing in listings:
                try:
                    enriched.append(client.fetch_listing_detail(listing))
                except Exception:
                    logging.exception("Failed to enrich listing %s", listing.ad_id)
                    enriched.append(listing)
        return enriched

    return await asyncio.to_thread(fetch)


class ProfileCheckAlreadyRunning(RuntimeError):
    """Raised when a chat already has a Kufar request in progress."""


async def run_profile_check(
    profile: UserProfile,
    operation: Callable[[], Awaitable[T]],
    *,
    skip_if_running: bool = False,
) -> T:
    """Run one network operation per chat to avoid duplicate Kufar requests."""
    lock = profile_lock(profile.id or profile.chat_id)
    if lock.locked() and skip_if_running:
        raise ProfileCheckAlreadyRunning
    if lock.locked():
        raise ProfileCheckAlreadyRunning
    async with lock:
        return await operation()


def profile_lock(chat_id: int) -> asyncio.Lock:
    """Return the in-memory lock that protects one chat from parallel checks."""
    lock = profile_locks.get(chat_id)
    if lock is None:
        lock = asyncio.Lock()
        profile_locks[chat_id] = lock
    return lock


def bot_kufar_client() -> KufarClient:
    """Create a Kufar client tuned for interactive bot responsiveness."""
    return KufarClient(
        timeout_seconds=settings.bot_fetch_timeout_seconds,
        retries=settings.bot_fetch_retries,
        retry_delay_seconds=settings.bot_fetch_retry_delay_seconds,
    )


def build_preview_listing() -> Listing:
    """Build a stable fake listing for Telegram preview mode."""
    return Listing(
        ad_id=0,
        title="Сдам комнату рядом с метро",
        url="https://re.kufar.by/vi/preview",
        price_usd=180,
        address="Притыцкого ул, 77, Минск",
        rooms="1",
        area_m2=17,
        floor="5",
        total_floors="9",
        metro=["Каменная Горка"],
        description=(
            "Светлая комната в аккуратной квартире. Есть кровать, рабочий стол, "
            "шкаф, стиральная машина и быстрый интернет. До метро несколько "
            "минут пешком. Это тестовое уведомление, оно не приходит с Kufar."
        ),
        published_at=datetime(2026, 5, 7, 20, 23, tzinfo=UTC),
        images=[ListingImage(gallery_url=settings.bot_preview_image_url)],
    )


async def send_listing(bot: Bot, chat_id: int, listing: Listing) -> None:
    """Send one listing from the background notifier."""
    presentation = build_listing_presentation(
        listing,
        max_images=settings.bot_max_images,
    )
    if presentation.image_urls:
        if len(presentation.image_urls) == 1:
            await bot.send_photo(
                chat_id,
                presentation.image_urls[0],
                caption=presentation.caption,
            )
            return
        await bot.send_media_group(chat_id, media_group_from_presentation(presentation))
        return
    await bot.send_message(
        chat_id,
        presentation.caption,
        disable_web_page_preview=True,
    )


def media_group_from_presentation(
    presentation: ListingPresentation,
) -> list[InputMediaPhoto]:
    """Build a Telegram album with a caption only on the first photo."""
    return [
        InputMediaPhoto(
            media=url,
            caption=presentation.caption if index == 0 else None,
        )
        for index, url in enumerate(presentation.image_urls)
    ]


def ensure_default_profile(chat_id: int) -> UserProfile:
    """Return a profile configured for Minsk rental search by default."""
    profile = storage.get(chat_id)
    defaults = default_request()
    profile.request = replace_request(
        profile.request,
        city=defaults.city,
        deal=defaults.deal,
        currency=defaults.currency,
        sort=defaults.sort,
        text=None,
        extra_params={},
    )
    storage.update(profile)
    return profile


def replace_request(request: SearchRequest, **changes: object) -> SearchRequest:
    """Create a modified SearchRequest without mutating the original object."""
    data = {
        "city": request.city,
        "deal": request.deal,
        "property_type": request.property_type,
        "rooms": request.rooms,
        "min_price": request.min_price,
        "max_price": request.max_price,
        "currency": request.currency,
        "text": request.text,
        "district": request.district,
        "metro": request.metro,
        "include_keywords": list(request.include_keywords),
        "exclude_keywords": list(request.exclude_keywords),
        "sort": request.sort,
        "size": request.size,
        "extra_params": dict(request.extra_params),
    }
    data.update(changes)
    return SearchRequest(**data)


def parse_subscription_callback(data: str) -> tuple[int, str, str | None]:
    """Parse subscription callback data in ``s:<id>:<action>[:value]`` form."""
    parts = data.split(":", maxsplit=3)
    if len(parts) < 3 or parts[0] != "s":
        raise ValueError(f"Invalid subscription callback: {data}")
    return int(parts[1]), parts[2], parts[3] if len(parts) == 4 else None


def restart_subscription_watch(subscription: UserProfile) -> None:
    """Reset watch start when filters change while monitoring is active."""
    subscription.watch_started_at = datetime.now(UTC) if subscription.enabled else None
    subscription.seen_ids = []


def apply_option_value(
    subscription: UserProfile,
    action: str,
    value: str | None,
) -> None:
    """Apply one inline filter option to a subscription."""
    if value is None:
        raise ValueError("Missing filter value")
    request = subscription.request
    if action == "set_type":
        target = target_by_code(value)
        subscription.request = replace_request(request, **target.request_patch)
        return
    if action == "set_price":
        price_range = price_range_by_code(value)
        subscription.request = replace_request(
            request,
            min_price=price_range.min_price,
            max_price=price_range.max_price,
        )
        return
    if action == "set_rooms":
        option = room_option_by_code(value)
        subscription.request = replace_request(request, rooms=option.value)
        return
    if action == "set_district":
        option = district_option_by_code(value)
        subscription.request = replace_request(request, district=option.value)
        return
    if action == "set_metro":
        option = metro_option_by_code(value)
        subscription.request = replace_request(request, metro=option.value)
        return
    raise ValueError(f"Unknown filter action: {action}")


def apply_text_input(
    request: SearchRequest,
    action: str,
    text: str,
) -> SearchRequest:
    """Apply a free-text filter value to a search request."""
    if action == "include":
        return replace_request(request, include_keywords=parse_keywords(text))
    if action == "exclude":
        return replace_request(request, exclude_keywords=parse_keywords(text))
    if action == "custom_price":
        min_price, max_price = parse_price_range_text(text)
        return replace_request(request, min_price=min_price, max_price=max_price)
    raise ValueError(f"Unknown text input action: {action}")


def parse_keywords(text: str) -> list[str]:
    """Parse comma/newline separated words or phrases."""
    items = [
        item.strip()
        for chunk in text.splitlines()
        for item in chunk.split(",")
        if item.strip()
    ]
    if not items:
        raise ValueError("укажи хотя бы одно слово")
    return list(dict.fromkeys(items))


def parse_price_range_text(text: str) -> tuple[int | None, int | None]:
    """Parse a user-entered price range like ``150-250`` or ``до 300``."""
    clean = text.replace("$", "").replace("USD", "").strip().lower()
    if clean.startswith("до"):
        return None, int(clean.removeprefix("до").strip())
    if "-" in clean:
        left, right = clean.split("-", maxsplit=1)
        min_price = int(left.strip()) if left.strip() else None
        max_price = int(right.strip()) if right.strip() else None
        return min_price, max_price
    value = int(clean)
    return None, value


def main_menu_keyboard(_profile: UserProfile | None = None) -> InlineKeyboardMarkup:
    """Build the main inline keyboard for navigation."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📌 Мои поиски",
                    callback_data=CALLBACK_SEARCHES,
                )
            ],
            [
                InlineKeyboardButton(
                    text="➕ Создать поиск",
                    callback_data=CALLBACK_NEW_SEARCH,
                )
            ],
            [InlineKeyboardButton(text="❔ Помощь", callback_data=CALLBACK_HELP)],
        ]
    )


def searches_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    """Build a keyboard with all saved searches for one chat."""
    rows = [
        [
            InlineKeyboardButton(
                text=subscription_button_title(subscription),
                callback_data=f"s:{subscription.id}:open",
            )
        ]
        for subscription in storage.list_subscriptions(chat_id)
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text="➕ Создать поиск",
                callback_data=CALLBACK_NEW_SEARCH,
            )
        ]
    )
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=CALLBACK_MAIN)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def subscription_keyboard(subscription: UserProfile) -> InlineKeyboardMarkup:
    """Build controls for one saved search."""
    watch_button = (
        InlineKeyboardButton(
            text="⏸ Выключить слежение",
            callback_data=f"s:{subscription.id}:watch_off",
        )
        if subscription.enabled
        else InlineKeyboardButton(
            text="🚀 Включить слежение",
            callback_data=f"s:{subscription.id}:watch_on",
        )
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⚙️ Фильтры",
                    callback_data=f"s:{subscription.id}:filters",
                )
            ],
            [watch_button],
            [
                InlineKeyboardButton(
                    text="🗑 Удалить",
                    callback_data=f"s:{subscription.id}:delete",
                )
            ],
            [InlineKeyboardButton(text="⬅️ К поискам", callback_data=CALLBACK_SEARCHES)],
        ]
    )


def subscription_filter_keyboard(subscription: UserProfile) -> InlineKeyboardMarkup:
    """Build filter controls for one saved search."""
    prefix = f"s:{subscription.id}"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🏠 Тип", callback_data=f"{prefix}:type"),
                InlineKeyboardButton(
                    text="🚪 Комнаты",
                    callback_data=f"{prefix}:rooms",
                ),
            ],
            [
                InlineKeyboardButton(text="💵 Цена", callback_data=f"{prefix}:price"),
                InlineKeyboardButton(text="🚇 Метро", callback_data=f"{prefix}:metro"),
            ],
            [
                InlineKeyboardButton(
                    text="📍 Район",
                    callback_data=f"{prefix}:district",
                )
            ],
            [
                InlineKeyboardButton(
                    text="➕ Ключевые слова",
                    callback_data=f"{prefix}:include",
                )
            ],
            [
                InlineKeyboardButton(
                    text="➖ Исключить слова",
                    callback_data=f"{prefix}:exclude",
                )
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{prefix}:open")],
        ]
    )


def filter_option_keyboard(
    subscription: UserProfile,
    action: str,
) -> InlineKeyboardMarkup:
    """Build option rows for one filter group."""
    prefix = f"s:{subscription.id}:set_{action}"
    if action == "type":
        rows = option_rows(
            [(target.code, target.title) for target in SEARCH_TARGETS],
            prefix,
        )
    elif action == "price":
        rows = option_rows(
            [(price.code, price.title) for price in PRICE_RANGES],
            prefix,
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text="✍️ Свой диапазон",
                    callback_data=f"s:{subscription.id}:custom_price",
                )
            ]
        )
    elif action == "rooms":
        rows = option_rows(
            [(option.code, option.title) for option in ROOM_OPTIONS],
            prefix,
        )
    elif action == "district":
        rows = option_rows(
            [(option.code, option.title) for option in DISTRICT_OPTIONS],
            prefix,
        )
    elif action == "metro":
        rows = option_rows(
            [(option.code, option.title) for option in METRO_OPTIONS],
            prefix,
        )
    else:
        rows = []
    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data=f"s:{subscription.id}:filters",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def option_rows(
    options: list[tuple[str, str]],
    callback_prefix: str,
) -> list[list[InlineKeyboardButton]]:
    """Build one-button rows for selectable options."""
    return [
        [InlineKeyboardButton(text=title, callback_data=f"{callback_prefix}:{code}")]
        for code, title in options
    ]


def filter_option_text(action: str) -> str:
    """Return the prompt text for one filter group."""
    labels = {
        "type": "🏠 <b>Тип жилья</b>",
        "price": "💵 <b>Цена</b>",
        "rooms": "🚪 <b>Количество комнат</b>",
        "district": "📍 <b>Район</b>",
        "metro": "🚇 <b>Метро</b>",
    }
    return labels.get(action, "⚙️ <b>Фильтр</b>")


def text_input_prompt(action: str) -> str:
    """Return a prompt for a free-text filter."""
    if action == "include":
        return (
            "➕ <b>Ключевые слова</b>\n\n"
            "Напиши слова или фразы через запятую. Объявление придёт только если "
            "они есть в заголовке, адресе, метро или описании."
        )
    if action == "exclude":
        return (
            "➖ <b>Исключающие слова</b>\n\n"
            "Напиши слова или фразы через запятую. Если они встретятся в объявлении, "
            "бот его пропустит."
        )
    return (
        "✍️ <b>Свой диапазон цены</b>\n\n"
        "Примеры: <code>150-250</code>, <code>до 300</code>, <code>500</code>."
    )


def filters_keyboard() -> InlineKeyboardMarkup:
    """Build the filter selection keyboard."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🏠 Тип жилья", callback_data=CALLBACK_TYPE),
                InlineKeyboardButton(text="💵 Цена", callback_data=CALLBACK_PRICE),
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=CALLBACK_MAIN)],
        ]
    )


def property_type_keyboard(profile: UserProfile) -> InlineKeyboardMarkup:
    """Build buttons for choosing the active search target."""
    current = target_for_request(profile.request).code
    rows = []
    for target in SEARCH_TARGETS:
        rows.append(
            [
                InlineKeyboardButton(
                    text=selected_label(target.title, current == target.code),
                    callback_data=f"set:type:{target.code}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=CALLBACK_FILTERS)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def price_keyboard(profile: UserProfile) -> InlineKeyboardMarkup:
    """Build buttons for choosing one predefined price range."""
    current_min = profile.request.min_price
    current_max = profile.request.max_price
    rows = []
    for price_range in PRICE_RANGES:
        is_selected = (
            price_range.min_price == current_min
            and price_range.max_price == current_max
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=selected_label(price_range.title, is_selected),
                    callback_data=f"set:price:{price_range.code}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=CALLBACK_FILTERS)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def selected_label(text: str, selected: bool) -> str:
    """Mark the currently selected option in button text."""
    return f"✅ {text}" if selected else text


def main_menu_text(chat_id: int) -> str:
    """Format the main screen text with a short summary of active filters."""
    subscriptions = storage.list_subscriptions(chat_id)
    enabled_count = sum(1 for item in subscriptions if item.enabled)
    return (
        "🏡 <b>Kufar Watch</b>\n"
        "Новые объявления по аренде в Минске без ручного просмотра выдачи.\n\n"
        f"📌 Поисков: <b>{len(subscriptions)}</b>\n"
        f"🔔 Активно: <b>{enabled_count}</b>\n\n"
        "Создай поиск, настрой фильтры и включи слежение."
    )


def subscriptions_text(chat_id: int) -> str:
    """Format all saved searches for one chat."""
    subscriptions = storage.list_subscriptions(chat_id)
    lines = ["📌 <b>Мои поиски</b>"]
    for index, subscription in enumerate(subscriptions, start=1):
        lines.append(
            "\n"
            f"{index}. {subscription_button_title(subscription)}\n"
            f"{subscription_summary(subscription)}"
        )
    return "\n".join(lines)


def status_text(chat_id: int) -> str:
    """Format runtime status for one chat."""
    subscriptions = storage.list_subscriptions(chat_id)
    enabled = [subscription for subscription in subscriptions if subscription.enabled]
    return (
        "📊 <b>Статус бота</b>\n\n"
        f"🆔 Chat ID: <code>{chat_id}</code>\n"
        f"📌 Поисков: <b>{len(subscriptions)}</b>\n"
        f"🔔 Активных: <b>{len(enabled)}</b>\n"
        f"⏱ Интервал проверки: <b>{settings.bot_poll_interval_seconds:g} сек.</b>\n"
        f"🖼 Фото в уведомлении: <b>{settings.bot_max_images}</b>\n"
        f"🧪 Preview: <b>{'включён' if settings.bot_enable_preview else 'выключен'}</b>"
    )


def subscription_text(subscription: UserProfile) -> str:
    """Format one saved search for display."""
    return (
        f"📌 <b>{escape(subscription.title)}</b>\n\n"
        f"{settings_text(subscription)}"
    )


def subscription_filters_text(subscription: UserProfile) -> str:
    """Format filter menu text for one saved search."""
    return (
        f"⚙️ <b>Фильтры: {escape(subscription.title)}</b>\n\n"
        "Доступны: тип, район, метро, комнаты, цена, ключевые и исключающие слова.\n"
        "После изменения фильтра старые объявления снова не отправляются.\n\n"
        f"{settings_text(subscription)}"
    )


def subscription_button_title(subscription: UserProfile) -> str:
    """Build a compact button label for one saved search."""
    status = "✅" if subscription.enabled else "⏸"
    return f"{status} {subscription.title}"


def subscription_summary(subscription: UserProfile) -> str:
    """Build a compact settings summary for a search list."""
    request = subscription.request
    return (
        f"🏠 {property_type_title(request)}, 💵 {price_title(request)}, "
        f"🚇 {request.metro or 'любое метро'}"
    )


def filters_menu_text(profile: UserProfile) -> str:
    """Format the filter menu text."""
    return (
        "⚙️ <b>Фильтры уведомлений</b>\n\n"
        "Сейчас можно выбрать тип жилья и диапазон цены.\n"
        "После изменения фильтров старые объявления будут забыты.\n\n"
        f"{settings_text(profile)}"
    )


def settings_text(profile: UserProfile) -> str:
    """Format the current profile settings for display in Telegram."""
    request = profile.request
    params = "&".join(f"{key}={value}" for key, value in request.params().items())
    return (
        f"🔔 <b>Статус:</b> {status_title(profile)}\n"
        "📍 <b>Город:</b> Минск\n"
        "🤝 <b>Сделка:</b> аренда\n"
        f"🏠 <b>Тип:</b> {property_type_title(request)}\n"
        f"🚪 <b>Комнаты:</b> {rooms_title(request)}\n"
        f"💵 <b>Цена:</b> {price_title(request)}\n"
        f"📍 <b>Район:</b> {request.district or 'любой'}\n"
        f"🚇 <b>Метро:</b> {request.metro or 'любое'}\n"
        f"➕ <b>Ключевые:</b> {keywords_title(request.include_keywords)}\n"
        f"➖ <b>Исключить:</b> {keywords_title(request.exclude_keywords)}\n"
        f"🔎 <b>Поиск:</b> <code>{escape(request.path())}?{escape(params)}</code>"
    )


def status_title(profile: UserProfile) -> str:
    """Convert monitoring state into a short Telegram status label."""
    return "активно ✅" if profile.enabled else "выключено ⏸"


def property_type_title(request: SearchRequest) -> str:
    """Convert the current request target into Russian UI text."""
    return target_for_request(request).title.lower()


def price_title(request: SearchRequest) -> str:
    """Convert the request price range into human-readable text."""
    if request.min_price is None and request.max_price is None:
        return "любая"
    min_price = request.min_price if request.min_price is not None else 0
    max_price = request.max_price if request.max_price is not None else "без лимита"
    return f"{min_price}-{max_price} {request.currency}"


def rooms_title(request: SearchRequest) -> str:
    """Convert room count into human-readable text."""
    return f"{request.rooms}" if request.rooms is not None else "любое"


def keywords_title(keywords: list[str]) -> str:
    """Format keyword list for Telegram settings."""
    return ", ".join(escape(keyword) for keyword in keywords) if keywords else "нет"


@router.errors()
async def errors(event) -> None:
    """Handle user-facing validation errors and log unexpected failures."""
    update = event.update
    message = getattr(update, "message", None)
    if message is not None and isinstance(event.exception, ValueError):
        await message.answer(str(event.exception))
        return
    logging.exception("Bot error", exc_info=event.exception)


async def run_bot() -> None:
    """Create the aiogram dispatcher and run long polling."""
    telegram_bot_token = settings.telegram_bot_token_value
    if not telegram_bot_token:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN in environment or .env file.")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        storage.check_connection()
    except SQLAlchemyError as error:
        raise RuntimeError(
            "PostgreSQL is unavailable. For normal use run the whole stack with "
            "`docker compose up -d --build`. Use local `kufarpars-bot` only when "
            "PostgreSQL is reachable from your host on localhost:5432."
        ) from error
    bot = Bot(
        token=telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher()
    router.message.middleware(AccessMiddleware())
    router.callback_query.middleware(AccessMiddleware())
    dispatcher.include_router(router)
    stop_event = asyncio.Event()
    notifier_task = asyncio.create_task(notifier_loop(bot, stop_event))
    try:
        await dispatcher.start_polling(bot)
    finally:
        stop_event.set()
        notifier_task.cancel()
        with suppress(asyncio.CancelledError):
            await notifier_task
        await bot.session.close()
        storage.close()


def main() -> None:
    """Run the bot entry point used by the console script."""
    try:
        asyncio.run(run_bot())
    except RuntimeError as error:
        print(error)
        exit(1)


if __name__ == "__main__":
    main()
