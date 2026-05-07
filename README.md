# KufarPars

Python project scaffold for parsing and processing Kufar listings.

## Quick start

```bash
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
pytest
```

## Run

```bash
kufarpars --help
```

Search apartments:

```bash
kufarpars search --rooms 1 --max-price 500 --pages 1
kufarpars search --rooms 2 --text "возле метро" --format json
kufarpars search --type room --city minsk --max-price 250
kufarpars search --deal buy --city minsk --sort cheap --format csv
```

Raw Kufar filter parameters can be passed with `--param KEY=VALUE`, for example:

```bash
kufarpars search --param mee=v.or:6 --param fli=v.or:6
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
- price range: preset ranges in USD
- fixed search area: rent in Minsk
- notifications include gallery photos when Kufar provides them
- full descriptions are loaded from listing detail pages before sending

After `/start`, use the buttons:

- `Настроить фильтры`
- `Тип жилья`
- `Цена`
- `Проверить сейчас`
- `Включить слежение`
- `Выключить слежение`
- `Мои фильтры`

## Project structure

- `models.py` contains parser-independent domain objects.
- `client.py` owns HTTP access, pagination, and detail-page enrichment.
- `parser.py` extracts search and detail data from Kufar Next.js payloads.
- `search_catalog.py` lists bot-visible search targets and filter presets.
- `telegram_formatting.py` builds Telegram-safe listing cards and captions.
- `telegram_bot.py` contains aiogram handlers and background monitoring.
- `bot_storage.py` stores chat settings and seen listing ids.

To add a new parser target, start with `search_catalog.py`, then teach
`client.py`/`parser.py` how to build and parse that target if its data shape
differs from real estate listings.

## Configuration

Copy `.env.example` to `.env` and adjust values for local use.
