# KufarPars

Telegram bot for monitoring new Kufar real-estate listings.

## Quick start

```bash
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
pytest
```

## Telegram bot

Create a bot with BotFather, put the token into `.env`, then run:

```bash
kufarpars-bot
```

The bot is controlled with inline buttons. Start it in Telegram:

```text
/start
```

Current bot filters:

- property type: apartment or room
- district and metro presets for Minsk
- room count
- price range: preset ranges in USD or custom user-entered range
- include keywords: required words or phrases in title/address/description
- exclude keywords: words or phrases that make a listing ignored
- fixed search area: rent in Minsk
- notifications include gallery photos when Kufar provides them
- full descriptions are loaded from listing detail pages before sending

For local UI testing without waiting for a new Kufar listing, enable preview
mode in `.env`, restart the bot, and send `/preview` in Telegram:

```env
KUFARPARS_BOT_ENABLE_PREVIEW=true
```

After `/start`, use the buttons:

- `Мои поиски`
- `Создать поиск`
- `Фильтры`
- `Включить слежение`
- `Выключить слежение`
- `Удалить поиск`

Useful commands:

- `/status` shows saved-search and monitoring status.
- `/preview` sends a fake listing when preview mode is enabled.

## Project structure

- `models.py` contains parser-independent domain objects.
- `client.py` owns HTTP access, pagination, and detail-page enrichment.
- `parser.py` extracts search and detail data from Kufar Next.js payloads.
- `search_catalog.py` lists bot-visible search targets and filter presets.
- `telegram_formatting.py` builds Telegram-safe listing cards and captions.
- `telegram_bot.py` contains aiogram handlers and background monitoring.
- `db.py` defines SQLAlchemy tables for chats, subscriptions, seen ads, and logs.
- `bot_storage.py` stores chat settings and seen listing ids through SQLAlchemy.

To add a new parser target, start with `search_catalog.py`, then teach
`client.py`/`parser.py` how to build and parse that target if its data shape
differs from real estate listings.

## Storage

The bot uses PostgreSQL only. Docker Compose builds the database URL from
`POSTGRES_DB`, `POSTGRES_USER`, and `POSTGRES_PASSWORD`, so you do not need to
write `KUFARPARS_DATABASE_URL` in `.env` for normal deployment:

```env
POSTGRES_DB=kufarpars
POSTGRES_USER=kufarpars
POSTGRES_PASSWORD=change-me
KUFARPARS_SEEN_TTL_DAYS=60
KUFARPARS_MAX_SEEN_PER_CHAT=5000
KUFARPARS_BOT_MAX_NOTIFICATIONS_PER_CHECK=5
KUFARPARS_BOT_INITIAL_POLL_DELAY_SECONDS=10
KUFARPARS_BOT_FETCH_TIMEOUT_SECONDS=8
KUFARPARS_BOT_FETCH_RETRIES=1
KUFARPARS_BOT_FETCH_RETRY_DELAY_SECONDS=1
KUFARPARS_BOT_DISPLAY_TIMEZONE=Europe/Minsk
KUFARPARS_BOT_ENABLE_PREVIEW=false
KUFARPARS_BOT_PREVIEW_IMAGE_URL=https://placehold.co/1200x800/png?text=Kufar+Preview
KUFARPARS_ALLOWED_CHAT_IDS=
```

Tables:

- `chats` stores Telegram chats.
- `subscriptions` stores multiple saved search settings per chat.
- `seen_ads` stores seen listing ids per subscription.
- `notification_logs` stores notification send attempts for diagnostics.

Schema migrations are managed with Alembic:

```bash
alembic upgrade head
```

Docker Compose includes PostgreSQL and passes the runtime database URL to the
bot container automatically:

```bash
docker compose up -d
```

The Docker image runs `alembic upgrade head` before starting the bot. You can
still run migrations manually with `docker compose run --rm bot alembic upgrade
head` when you want to check them separately.

`KUFARPARS_BOT_MAX_NOTIFICATIONS_PER_CHECK` limits how many new listings are
enriched with detail-page data and sent during one background check. The bot
still marks the current search page as seen, so a server restart or first run
does not flood the chat with the whole backlog.

## Configuration

Copy `.env.example` to `.env` and adjust values for local use. Runtime
configuration is validated with Pydantic Settings: invalid numbers, blank
required strings, or an unknown timezone fail fast at startup, and the Telegram
token is handled as a secret value.

Set `KUFARPARS_ALLOWED_CHAT_IDS` to a comma-separated list of Telegram chat ids
when the bot should be private.
