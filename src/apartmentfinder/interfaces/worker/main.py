"""Background polling worker entry point."""

from __future__ import annotations

import asyncio
import logging
import signal
from contextlib import suppress

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from sqlalchemy.exc import SQLAlchemyError

from apartmentfinder.infrastructure.config import settings
from apartmentfinder.infrastructure.health import HealthState, start_health_server
from apartmentfinder.interfaces.telegram import bot as telegram_bot
from apartmentfinder.interfaces.telegram.bot import configure_logging, notifier_loop

logger = logging.getLogger(__name__)


async def run_worker() -> None:
    """Run periodic subscription polling without Telegram update handlers."""
    telegram_bot_token = settings.telegram_bot_token_value
    if not telegram_bot_token:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN in environment or .env file.")
    configure_logging()
    logger.info("worker_starting log_level=%s", settings.log_level)
    try:
        telegram_bot.storage.check_connection()
    except SQLAlchemyError as error:
        raise RuntimeError(
            "PostgreSQL is unavailable. For Docker use run "
            "`docker compose up -d --build`. For local worker runs, PostgreSQL "
            "must be reachable from your host."
        ) from error

    bot = Bot(
        token=telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    stop_event = asyncio.Event()
    health_state = HealthState(
        role="worker",
        check_database=telegram_bot.storage.check_connection,
        require_recent_poll=True,
        poll_max_age_seconds=settings.readiness_poll_max_age_seconds,
    )
    telegram_bot.worker_health_state = health_state
    health_server = start_health_server(
        settings.health_host,
        settings.worker_health_port,
        health_state,
    )
    install_signal_handlers(stop_event)
    try:
        await notifier_loop(bot, stop_event)
    finally:
        stop_event.set()
        telegram_bot.worker_health_state = None
        health_server.close()
        await bot.session.close()
        telegram_bot.storage.close()


def install_signal_handlers(stop_event: asyncio.Event) -> None:
    """Stop the worker gracefully on container and terminal shutdown signals."""
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop_event.set)


def main() -> None:
    """Run the worker entry point used by the console script."""
    try:
        asyncio.run(run_worker())
    except RuntimeError as error:
        logger.error("worker_start_failed error=%s", error)
        exit(1)


if __name__ == "__main__":
    main()
