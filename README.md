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
Docker stack. The stack runs three services: PostgreSQL, the Telegram bot, and
the background polling worker:

```bash
docker compose up -d --build
```

The `bot` service handles Telegram commands, settings, favorites, history, and
manual user actions. The `worker` service periodically checks enabled saved
searches and sends listing notifications.

If you intentionally run the app from the local virtualenv, PostgreSQL must be
reachable from the host on `localhost:5432`. Start the bot and worker in
separate terminals:

```bash
docker compose up -d postgres
alembic upgrade head
apartmentfinder-bot
```

```bash
apartmentfinder-worker
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
APARTMENTFINDER_LOG_LEVEL=INFO
APARTMENTFINDER_BROWSER_FETCH_ENABLED=false
APARTMENTFINDER_BROWSER_FETCH_TIMEOUT_SECONDS=20
APARTMENTFINDER_BROWSER_FETCH_WAIT_UNTIL=networkidle
APARTMENTFINDER_BROWSER_FETCH_FALLBACK_ON_EMPTY=true
APARTMENTFINDER_HEALTH_HOST=0.0.0.0
APARTMENTFINDER_BOT_HEALTH_PORT=8080
APARTMENTFINDER_WORKER_HEALTH_PORT=8081
APARTMENTFINDER_READINESS_POLL_MAX_AGE_SECONDS=900
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
Docker Compose runs the same migration command before starting both the `bot`
and `worker` services.

## Configuration

Copy `.env.example` to `.env` and adjust values for local use. Runtime
configuration is validated with Pydantic Settings: invalid numbers, blank
required strings, or an unknown timezone fail fast at startup, and the Telegram
token is handled as a secret value.

Set `APARTMENTFINDER_ALLOWED_CHAT_IDS` to a comma-separated list of Telegram
chat ids when the bot should be private.

### Health checks

Both runtime processes expose lightweight operational HTTP endpoints:

- `/health` returns `200` when the process is alive.
- `/readiness` checks configuration, PostgreSQL, a queue placeholder for future
  workers, and the worker's last successful polling tick.

Default local endpoints:

```bash
curl http://127.0.0.1:8080/health
curl http://127.0.0.1:8080/readiness
curl http://127.0.0.1:8081/health
curl http://127.0.0.1:8081/readiness
```

The worker readiness endpoint returns `503` until the first polling cycle
completes, and later returns `503` if the last successful poll is older than
`APARTMENTFINDER_READINESS_POLL_MAX_AGE_SECONDS`.

### Logging

Logs are written to stdout and are visible with Docker Compose:

```bash
docker compose logs -f bot
docker compose logs -f worker
```

Use `INFO` for normal server usage and switch to `DEBUG` while developing or
diagnosing source parsing:

```env
APARTMENTFINDER_LOG_LEVEL=DEBUG
```

After changing `.env`, restart the bot container:

```bash
docker compose restart bot worker
```

Debug logs include source checks, HTTP statuses, response times, parsed listing
counts, filter rejection reasons, and notification counts. Secrets such as the
Telegram token, database password, and full HTML responses are not logged.

After changing `.env`, rebuild the bot container:

```bash
docker compose up -d --build
docker compose logs -f bot worker
```

### CloakBrowser fallback

The bot can optionally use the CloakBrowser Python package as a browser-backed
fallback when normal HTTP fetching fails or a source page looks suspiciously
empty. It is disabled by default:

```env
APARTMENTFINDER_BROWSER_FETCH_ENABLED=true
```

The Docker stack stores CloakBrowser's downloaded browser files in the
`cloakbrowser_cache` volume, so the first browser fallback may take longer while
Chromium is downloaded, but later deploys can reuse the cached binary.
CloakBrowser's Python API and install behavior are documented at
<https://github.com/CloakHQ/CloakBrowser>.
