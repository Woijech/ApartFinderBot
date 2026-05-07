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
from typing import TypeVar

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
)

from kufarpars.bot_storage import BotStorage, UserProfile
from kufarpars.client import KufarClient, KufarNetworkError, SearchRequest
from kufarpars.config import settings
from kufarpars.models import Listing, ListingImage
from kufarpars.search_catalog import (
    PRICE_RANGES,
    SEARCH_TARGETS,
    default_request,
    price_range_by_code,
    target_by_code,
    target_for_request,
)
from kufarpars.telegram_formatting import (
    ListingPresentation,
    build_listing_presentation,
)

router = Router()
T = TypeVar("T")
profile_locks: dict[int, asyncio.Lock] = {}
storage = BotStorage(
    settings.bot_db_path,
    legacy_json_path=settings.legacy_bot_state_path,
    seen_ttl_days=settings.seen_ttl_days,
    max_seen_per_chat=settings.max_seen_per_chat,
)

CALLBACK_MAIN = "menu:main"
CALLBACK_FILTERS = "menu:filters"
CALLBACK_TYPE = "filter:type"
CALLBACK_PRICE = "filter:price"
CALLBACK_WATCH_ON = "action:watch_on"
CALLBACK_WATCH_OFF = "action:watch_off"
CALLBACK_SETTINGS = "action:settings"


@router.message(Command("start", "menu"))
async def start(message: Message) -> None:
    """Open the main menu and create a default profile if needed."""
    profile = ensure_default_profile(message.chat.id)
    await message.answer(
        main_menu_text(profile),
        reply_markup=main_menu_keyboard(profile),
        disable_web_page_preview=True,
    )


@router.message(Command("settings"))
async def show_settings(message: Message) -> None:
    """Show current settings for users who prefer a command."""
    profile = ensure_default_profile(message.chat.id)
    await message.answer(
        settings_text(profile),
        reply_markup=main_menu_keyboard(profile),
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


@router.callback_query(F.data == CALLBACK_MAIN)
async def main_menu(callback: CallbackQuery) -> None:
    """Render the main menu after a user presses back/home."""
    profile = ensure_default_profile(callback.message.chat.id)
    await callback.message.edit_text(
        main_menu_text(profile),
        reply_markup=main_menu_keyboard(profile),
        disable_web_page_preview=True,
    )
    await callback.answer()


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
    try:
        listings = await run_profile_check(profile, lambda: fetch_listings(profile))
    except KufarNetworkError:
        logging.warning("Kufar is temporarily unavailable before enabling watch")
        await callback.message.answer(
            "Не смог проверить Kufar перед включением слежения. "
            "Попробуй включить ещё раз чуть позже.",
            reply_markup=main_menu_keyboard(profile),
        )
        return
    except ProfileCheckAlreadyRunning:
        await callback.message.answer(
            "Сейчас уже идёт проверка Kufar. "
            "Попробуй включить слежение через пару секунд.",
            reply_markup=main_menu_keyboard(profile),
        )
        return
    profile.enabled = True
    profile.watch_started_at = datetime.now(UTC)
    storage.mark_seen(profile.chat_id, [listing.ad_id for listing in listings])
    profile.seen_ids = storage.recent_seen_ids(profile.chat_id)
    storage.update(profile)
    await callback.message.edit_text(
        "✅ <b>Слежение включено</b>\n\n"
        "Текущую выдачу запомнил. Дальше буду присылать только новые объявления.\n\n"
        f"📌 Запомнено объявлений: <b>{len(profile.seen_ids)}</b>",
        reply_markup=main_menu_keyboard(profile),
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
    try:
        listings = await run_profile_check(
            profile,
            lambda: fetch_listings(profile),
            skip_if_running=True,
        )
    except ProfileCheckAlreadyRunning:
        return
    except KufarNetworkError:
        logging.warning("Failed to check Kufar for chat %s", profile.chat_id)
        return

    if profile.watch_started_at is None:
        profile.watch_started_at = datetime.now(UTC)
        storage.mark_seen(profile.chat_id, [listing.ad_id for listing in listings])
        profile.seen_ids = storage.recent_seen_ids(profile.chat_id)
        storage.update(profile)
        return

    fresh_listings = listings_after_watch_start(profile, listings)
    unseen_ids = set(
        storage.unseen_ids(
            profile.chat_id,
            [listing.ad_id for listing in fresh_listings],
        )
    )
    new_listings = [
        listing for listing in fresh_listings if listing.ad_id in unseen_ids
    ]
    if not new_listings:
        storage.mark_seen(profile.chat_id, [listing.ad_id for listing in listings])
        return

    notification_limit = max(0, settings.bot_max_notifications_per_check)
    listings_to_send = new_listings[:notification_limit]

    enriched = await fetch_listing_details(list(reversed(listings_to_send)))
    for listing in enriched:
        await send_listing(bot, profile.chat_id, listing)
    storage.mark_seen(profile.chat_id, [listing.ad_id for listing in listings])
    profile.seen_ids = storage.recent_seen_ids(profile.chat_id)
    storage.update(profile)


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
    lock = profile_lock(profile.chat_id)
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
        "sort": request.sort,
        "size": request.size,
        "extra_params": dict(request.extra_params),
    }
    data.update(changes)
    return SearchRequest(**data)


def main_menu_keyboard(profile: UserProfile) -> InlineKeyboardMarkup:
    """Build the main inline keyboard for navigation and monitoring actions."""
    watch_button = (
        InlineKeyboardButton(
            text="⏸ Выключить слежение",
            callback_data=CALLBACK_WATCH_OFF,
        )
        if profile.enabled
        else InlineKeyboardButton(
            text="🚀 Включить слежение",
            callback_data=CALLBACK_WATCH_ON,
        )
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⚙️ Фильтры",
                    callback_data=CALLBACK_FILTERS,
                )
            ],
            [watch_button],
            [
                InlineKeyboardButton(
                    text="📋 Мои настройки",
                    callback_data=CALLBACK_SETTINGS,
                )
            ],
        ]
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


def main_menu_text(profile: UserProfile) -> str:
    """Format the main screen text with a short summary of active filters."""
    return (
        "🏡 <b>Kufar Watch</b>\n"
        "Новые объявления по аренде в Минске без ручного просмотра выдачи.\n\n"
        "Настрой фильтры и включи слежение. Когда появится новое объявление, "
        "я пришлю карточку сюда.\n\n"
        f"{settings_text(profile)}"
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
        f"💵 <b>Цена:</b> {price_title(request)}\n"
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
    if not settings.telegram_bot_token:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN in environment or .env file.")
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher()
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
