# ApartmentFinder

Telegram bot for monitoring rental listings from multiple real-estate sources.
Kufar and Realt.by are source adapters, not the center of the application.

## Quick Start

```bash
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
pytest
```

## Telegram Bot

Create a bot with BotFather, put the token into `.env`, then run the full
Docker stack:

```bash
docker compose up -d --build
```

If you intentionally run the bot from the local virtualenv, PostgreSQL must be
reachable from the host on `localhost:5432`:

```bash
docker compose up -d postgres
alembic upgrade head
apartmentfinder-bot
```

Current bot filters:

- property type: apartment or room
- district and metro presets for Minsk
- room count
- price range: preset ranges in USD or custom user-entered range
- include keywords: required words or phrases in title/address/description
- exclude keywords: words or phrases that make a listing ignored
- fixed search area: rent in Minsk
- notifications include gallery photos when a source provides them
- full descriptions are loaded from listing detail pages before sending

## Architecture

The project is organized around source-neutral application rules:

- `domain/` contains pure data models such as `Listing` and `SearchRequest`.
- `application/` contains ports, filtering, monitoring helpers, and source
  registry orchestration.
- `infrastructure/sources/<site>/` contains site-specific HTTP clients and
  parsers.
- `infrastructure/persistence/` contains SQLAlchemy tables and storage.
- `interfaces/telegram/` contains aiogram handlers, keyboards, and formatting.

To add a new source, create `infrastructure/sources/<site>/client.py`,
`parser.py`, and `source.py`, normalize output into `Listing`, then register the
source in `application/source_registry.py`. Telegram handlers and persistence
should not need source-specific changes.

## Storage

The bot uses PostgreSQL only. Docker Compose builds the database URL from
`POSTGRES_DB`, `POSTGRES_USER`, and `POSTGRES_PASSWORD`, so you normally do not
need to write `APARTMENTFINDER_DATABASE_URL` in `.env`:

```env
POSTGRES_DB=apartmentfinder
POSTGRES_USER=apartmentfinder
POSTGRES_PASSWORD=change-me
APARTMENTFINDER_KUFAR_BASE_URL=https://re.kufar.by
APARTMENTFINDER_REALT_BASE_URL=https://realt.by
APARTMENTFINDER_SEEN_TTL_DAYS=60
APARTMENTFINDER_MAX_SEEN_PER_CHAT=5000
APARTMENTFINDER_BOT_MAX_NOTIFICATIONS_PER_CHECK=5
APARTMENTFINDER_BOT_INITIAL_POLL_DELAY_SECONDS=10
APARTMENTFINDER_BOT_FETCH_TIMEOUT_SECONDS=8
APARTMENTFINDER_BOT_FETCH_RETRIES=1
APARTMENTFINDER_BOT_FETCH_RETRY_DELAY_SECONDS=1
APARTMENTFINDER_BOT_DISPLAY_TIMEZONE=Europe/Minsk
APARTMENTFINDER_ALLOWED_CHAT_IDS=
APARTMENTFINDER_HTTP_PROXY=
APARTMENTFINDER_LOG_LEVEL=INFO
APARTMENTFINDER_BROWSER_FETCH_ENABLED=false
APARTMENTFINDER_BROWSER_FETCH_CDP_URL=http://cloakbrowser:9222
APARTMENTFINDER_BROWSER_FETCH_TIMEOUT_SECONDS=20
APARTMENTFINDER_BROWSER_FETCH_WAIT_UNTIL=networkidle
APARTMENTFINDER_BROWSER_FETCH_FALLBACK_ON_EMPTY=true
APARTMENTFINDER_BROWSER_FETCH_FINGERPRINT_SEED=
```

Tables:

- `chats` stores Telegram chats.
- `subscriptions` stores saved search settings per chat.
- `seen_ads` stores seen listing ids per subscription and source.
- `notification_logs` stores notification send attempts for diagnostics.

Schema migrations are managed with Alembic:

```bash
alembic upgrade head
```

The Docker image runs `alembic upgrade head` before starting the bot.

## Configuration

Copy `.env.example` to `.env` and adjust values for local use. Runtime
configuration is validated with Pydantic Settings: invalid numbers, blank
required strings, or an unknown timezone fail fast at startup, and the Telegram
token is handled as a secret value.

Set `APARTMENTFINDER_ALLOWED_CHAT_IDS` to a comma-separated list of Telegram
chat ids when the bot should be private.

### Logging

Logs are written to stdout and are visible with Docker Compose:

```bash
docker compose logs -f bot
```

Use `INFO` for normal server usage and switch to `DEBUG` while developing or
diagnosing source parsing:

```env
APARTMENTFINDER_LOG_LEVEL=DEBUG
```

After changing `.env`, restart the bot container:

```bash
docker compose restart bot
```

Debug logs include source checks, HTTP statuses, response times, parsed listing
counts, filter rejection reasons, and notification counts. Secrets such as the
Telegram token, database password, proxy credentials, and full HTML responses
are not logged.

For geographic availability checks through your own proxy or gateway, set one
explicit proxy URL:

```env
APARTMENTFINDER_HTTP_PROXY=http://user:password@host:port
```

Leave it empty to connect directly from the server. After changing `.env`,
rebuild the bot container:

```bash
docker compose up -d --build
docker compose logs -f bot
```

### CloakBrowser fallback

The Docker stack includes an optional `cloakbrowser` sidecar using the official
`cloakhq/cloakbrowser` image in `cloakserve` mode. It is disabled by default in
the bot and is used only as a fallback when normal HTTP fetching fails or a
source page looks suspiciously empty:

```env
APARTMENTFINDER_BROWSER_FETCH_ENABLED=true
```

The bot connects to CloakBrowser over CDP at
`APARTMENTFINDER_BROWSER_FETCH_CDP_URL`, which defaults to
`http://cloakbrowser:9222` inside the Docker network. Do not publish CDP port
`9222` to the public internet: CDP gives full browser control. CloakBrowser's
official Docker/CDP usage is documented at
<https://github.com/CloakHQ/CloakBrowser>.
