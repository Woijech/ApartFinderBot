# ApartmentFinder

ApartmentFinder - Telegram-бот для мониторинга объявлений об аренде жилья в
Минске. Он получает объявления из Kufar и Realt.by, приводит их к общей модели,
применяет пользовательские фильтры и отправляет новые подходящие варианты в
Telegram.

Проект оформлен как небольшой сервис с разделением ответственности: Telegram
bot обрабатывает пользовательские команды, worker выполняет периодический
polling, PostgreSQL хранит состояние, а operational endpoints отдают
health/readiness и Prometheus metrics.

## Возможности

- Поиск объявлений из Kufar и Realt.by.
- Поддержка аренды квартир и комнат.
- Фильтры по району, метро, количеству комнат и цене.
- Include/exclude keywords для фильтрации по заголовку, адресу, метро и описанию.
- Несколько сохранённых поисков для одного Telegram-чата.
- Включение и выключение слежения отдельно для каждого поиска.
- История объявлений, которые уже подходили под фильтр.
- Избранные объявления.
- Блокировка продавцов.
- Отправка карточек с фото, если источник отдаёт изображения.
- Догрузка detail-page перед отправкой уведомления.
- Async fetching источников с ограничением конкуренции.
- Per-source rate limiting, cooldown после ошибок и отдельный лимит на browser fallback.
- Health, readiness и Prometheus metrics endpoints.
- Docker Compose запуск для `bot`, `worker` и `postgres`.

## Архитектура

Проект разделён на слои `domain / application / infrastructure / interfaces`.

| Слой | Назначение | Примеры |
| --- | --- | --- |
| `domain` | Чистые модели без зависимостей от Telegram, HTTP и БД. | `Listing`, `ListingImage`, `SearchRequest`, `SearchResult` |
| `application` | Правила приложения и orchestration без привязки к конкретным сайтам. | фильтрация, monitoring helpers, `ListingSource`, `fetch_from_sources` |
| `infrastructure` | Интеграции с внешним миром. | Kufar/Realt clients, parsers, SQLAlchemy storage, config, health, metrics, rate limiter |
| `interfaces` | Точки входа в приложение. | Telegram handlers, worker entrypoint |

Основной контракт между источниками и приложением - нормализованная модель
`Listing`. Благодаря этому Telegram-интерфейс, хранилище и логика фильтрации не
зависят от структуры HTML конкретного сайта.

### Runtime-сервисы

| Сервис | Ответственность |
| --- | --- |
| `bot` | Telegram-команды, меню, настройки, история, избранное, бан-лист продавцов. |
| `worker` | Периодический обход активных подписок и отправка уведомлений. |
| `postgres` | Хранение чатов, подписок, seen ads, истории, избранного, бан-листа и логов уведомлений. |

## Технологии

| Категория | Используется |
| --- | --- |
| Язык | Python `>=3.11` |
| Telegram | aiogram 3 |
| HTTP | httpx AsyncClient |
| HTML parsing | beautifulsoup4 |
| Browser fallback | cloakbrowser |
| Config | pydantic-settings |
| Database | PostgreSQL, SQLAlchemy 2, psycopg |
| Migrations | Alembic |
| Tests/lint | pytest, ruff |
| Runtime | Docker, Docker Compose |

## Быстрый старт через Docker Compose

Создайте Telegram-бота через BotFather и получите token.

Скопируйте пример окружения:

```bash
cp .env.example .env
```

Заполните минимум:

```env
TELEGRAM_BOT_TOKEN=123456:telegram-token
POSTGRES_PASSWORD=change-me
```

Запустите сервисы:

```bash
docker compose up -d --build
```

Проверка логов:

```bash
docker compose logs -f bot worker
```

Остановка:

```bash
docker compose down
```

PostgreSQL публикуется на `localhost:5432`. Данные сохраняются в Docker volume
`postgres_data`.

## Локальный запуск

Для локального запуска приложения можно использовать PostgreSQL из Compose, а
bot и worker запускать из virtualenv.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

```bash
docker compose up -d postgres
alembic upgrade head
```

Запуск Telegram bot:

```bash
apartmentfinder-bot
```

Запуск worker:

```bash
apartmentfinder-worker
```

Entry points объявлены в `pyproject.toml`:

```toml
apartmentfinder-bot = "apartmentfinder.interfaces.telegram.bot:main"
apartmentfinder-worker = "apartmentfinder.interfaces.worker.main:main"
```

## Переменные окружения

Runtime config загружается через Pydantic Settings. Большинство переменных
использует префикс `APARTMENTFINDER_`. Telegram token читается как
`TELEGRAM_BOT_TOKEN`.

### Основные настройки

| Переменная | По умолчанию | Назначение |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | пусто | Token Telegram-бота. |
| `APARTMENTFINDER_DATABASE_URL` | PostgreSQL localhost URL | SQLAlchemy URL подключения к PostgreSQL. |
| `POSTGRES_DB` | `apartmentfinder` | Имя БД для Docker Compose. |
| `POSTGRES_USER` | `apartmentfinder` | Пользователь PostgreSQL для Docker Compose. |
| `POSTGRES_PASSWORD` | `apartmentfinder` | Пароль PostgreSQL для Docker Compose. |
| `APARTMENTFINDER_ALLOWED_CHAT_IDS` | пусто | Comma-separated список разрешённых chat id. Если пусто, бот доступен всем. |
| `APARTMENTFINDER_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` или `ERROR`. |
| `APARTMENTFINDER_USER_AGENT` | `ApartmentFinder/0.1 (+local research parser)` | User-Agent для HTTP-запросов. |
| `APARTMENTFINDER_BOT_DISPLAY_TIMEZONE` | `Europe/Minsk` | Таймзона отображения дат. |

### Источники и HTTP

| Переменная | По умолчанию | Назначение |
| --- | --- | --- |
| `APARTMENTFINDER_KUFAR_BASE_URL` | `https://re.kufar.by` | Base URL Kufar. |
| `APARTMENTFINDER_REALT_BASE_URL` | `https://realt.by` | Base URL Realt.by. |
| `APARTMENTFINDER_TIMEOUT_SECONDS` | `20` | HTTP timeout source clients. |
| `APARTMENTFINDER_REQUEST_RETRIES` | `2` | Количество retries для source HTTP. |
| `APARTMENTFINDER_REQUEST_RETRY_DELAY_SECONDS` | `2` | Пауза между retries. |
| `APARTMENTFINDER_SOURCE_FETCH_CONCURRENCY` | `2` | Конкурентность обхода источников. |
| `APARTMENTFINDER_SUBSCRIPTION_CHECK_CONCURRENCY` | `3` | Конкурентность обработки подписок worker'ом. |

### Worker и уведомления

| Переменная | По умолчанию | Назначение |
| --- | --- | --- |
| `APARTMENTFINDER_BOT_POLL_INTERVAL_SECONDS` | `300` | Интервал polling worker'а. |
| `APARTMENTFINDER_BOT_INITIAL_POLL_DELAY_SECONDS` | `10` | Задержка перед первым polling tick. |
| `APARTMENTFINDER_BOT_MAX_NOTIFICATIONS_PER_CHECK` | `5` | Максимум уведомлений за одну проверку подписки. |
| `APARTMENTFINDER_BOT_FETCH_TIMEOUT_SECONDS` | `8` | Timeout вокруг проверки профиля. |
| `APARTMENTFINDER_BOT_FETCH_RETRIES` | `1` | Retries проверки профиля. |
| `APARTMENTFINDER_BOT_FETCH_RETRY_DELAY_SECONDS` | `1` | Пауза между retries проверки профиля. |
| `APARTMENTFINDER_BOT_MAX_PAGES` | `1` | Количество страниц источника за одну проверку. |
| `APARTMENTFINDER_BOT_PAGE_DELAY_SECONDS` | `1` | Пауза между страницами одного источника. |
| `APARTMENTFINDER_BOT_MAX_IMAGES` | `9` | Максимум фото в Telegram-карточке. |

### Хранение состояния

| Переменная | По умолчанию | Назначение |
| --- | --- | --- |
| `APARTMENTFINDER_SEEN_TTL_DAYS` | `60` | TTL для seen ads. |
| `APARTMENTFINDER_MAX_SEEN_PER_CHAT` | `5000` | Верхняя граница seen-записей на chat. |

### Browser fallback

| Переменная | По умолчанию | Назначение |
| --- | --- | --- |
| `APARTMENTFINDER_BROWSER_FETCH_ENABLED` | `false` | Включает browser-backed fallback через CloakBrowser. |
| `APARTMENTFINDER_BROWSER_FETCH_TIMEOUT_SECONDS` | `20` | Timeout browser fetch. |
| `APARTMENTFINDER_BROWSER_FETCH_WAIT_UNTIL` | `networkidle` | Wait mode: `commit`, `domcontentloaded`, `load`, `networkidle`. |
| `APARTMENTFINDER_BROWSER_FETCH_FALLBACK_ON_EMPTY` | `true` | Использовать fallback, если source вернул подозрительно пустую выдачу. |

### Per-source rate limiting

Nested env использует разделитель `__`.

| Переменная | По умолчанию | Назначение |
| --- | --- | --- |
| `APARTMENTFINDER_SOURCE_LIMITS__KUFAR__MAX_REQUESTS_PER_MINUTE` | `60` | Лимит обычных запросов Kufar в минуту. |
| `APARTMENTFINDER_SOURCE_LIMITS__KUFAR__MIN_DELAY` | `0.5` | Минимальная пауза между запросами Kufar. |
| `APARTMENTFINDER_SOURCE_LIMITS__KUFAR__MAX_DELAY` | `10` | Максимальная задержка/cooldown Kufar. |
| `APARTMENTFINDER_SOURCE_LIMITS__KUFAR__JITTER` | `0.2` | Случайная добавка к задержке. |
| `APARTMENTFINDER_SOURCE_LIMITS__KUFAR__COOLDOWN_AFTER_ERRORS` | `3` | Количество ошибок подряд до cooldown. |
| `APARTMENTFINDER_SOURCE_LIMITS__KUFAR__BROWSER_FALLBACK_LIMIT` | `3` | Лимит browser fallback попыток в минуту. |
| `APARTMENTFINDER_SOURCE_LIMITS__REALT__MAX_REQUESTS_PER_MINUTE` | `60` | Лимит обычных запросов Realt в минуту. |
| `APARTMENTFINDER_SOURCE_LIMITS__REALT__MIN_DELAY` | `0.5` | Минимальная пауза между запросами Realt. |
| `APARTMENTFINDER_SOURCE_LIMITS__REALT__MAX_DELAY` | `10` | Максимальная задержка/cooldown Realt. |
| `APARTMENTFINDER_SOURCE_LIMITS__REALT__JITTER` | `0.2` | Случайная добавка к задержке. |
| `APARTMENTFINDER_SOURCE_LIMITS__REALT__COOLDOWN_AFTER_ERRORS` | `3` | Количество ошибок подряд до cooldown. |
| `APARTMENTFINDER_SOURCE_LIMITS__REALT__BROWSER_FALLBACK_LIMIT` | `3` | Лимит browser fallback попыток в минуту. |

### Operational endpoints

| Переменная | По умолчанию | Назначение |
| --- | --- | --- |
| `APARTMENTFINDER_HEALTH_HOST` | `0.0.0.0` | Host lightweight HTTP server. |
| `APARTMENTFINDER_BOT_HEALTH_PORT` | `8080` | Port health сервера bot. |
| `APARTMENTFINDER_WORKER_HEALTH_PORT` | `8081` | Port health сервера worker. |
| `APARTMENTFINDER_READINESS_POLL_MAX_AGE_SECONDS` | `900` | Максимальный возраст последнего успешного poll для worker readiness. |

## База данных и Alembic

Сервис использует PostgreSQL. Миграции находятся в `migrations/versions`.

| Таблица | Что хранит |
| --- | --- |
| `chats` | Telegram chats. |
| `subscriptions` | Сохранённые поиски и JSON настроек фильтра. |
| `seen_ads` | Уже увиденные объявления по `subscription_id + source + ad_id`. |
| `notification_logs` | Логи попыток отправки уведомлений. |
| `listing_history` | Снимки объявлений, которые подходили под фильтр. |
| `banned_sellers` | Продавцы, скрытые пользователем. |
| `favorite_listings` | Избранные объявления пользователя. |

Применить миграции:

```bash
alembic upgrade head
```

Откатить одну миграцию:

```bash
alembic downgrade -1
```

Создать новую ревизию:

```bash
alembic revision --autogenerate -m "describe change"
```

В Docker Compose миграции выполняются перед стартом `bot` и `worker`.

## Тесты и проверки

Установить dev-зависимости:

```bash
python -m pip install -e ".[dev]"
```

Запустить линтер:

```bash
ruff check src tests
```

Запустить тесты:

```bash
pytest
```

Проверить Docker Compose конфигурацию:

```bash
docker compose config
```

Тесты покрывают архитектурные границы, config validation, Telegram handlers,
formatting, parsers, source clients, storage, health, metrics и rate limiter.

## Логирование и устойчивость

Логи пишутся в stdout:

```bash
docker compose logs -f bot
docker compose logs -f worker
```

Реализованные механизмы устойчивости:

- проверка PostgreSQL при старте bot и worker;
- retries и timeouts для HTTP-запросов;
- async fetching с ограничением конкуренции;
- per-source throttling, jitter и cooldown после серии ошибок;
- отдельный лимит на browser fallback;
- graceful shutdown worker'а по `SIGINT` и `SIGTERM`;
- source-level метрики ошибок и времени ответа;
- readiness worker'а с проверкой свежести последнего успешного polling tick;
- хранение Telegram token как secret value в config.

Health endpoints:

```bash
curl http://127.0.0.1:8080/health
curl http://127.0.0.1:8080/readiness
curl http://127.0.0.1:8081/health
curl http://127.0.0.1:8081/readiness
```

Prometheus metrics:

```bash
curl http://127.0.0.1:8080/metrics
curl http://127.0.0.1:8081/metrics
```

Текущие метрики:

- `subscription_check_duration_seconds`
- `source_response_time_seconds`
- `new_ads_found_total`
- `source_errors_total`
- `notifications_sent_total`
- `empty_results_total`

## Структура проекта

```text
.
|-- alembic.ini
|-- docker-compose.yml
|-- Dockerfile
|-- migrations/
|   |-- env.py
|   `-- versions/
|-- pyproject.toml
|-- src/
|   `-- apartmentfinder/
|       |-- application/
|       |   |-- filtering.py
|       |   |-- monitoring.py
|       |   |-- ports.py
|       |   `-- source_registry.py
|       |-- domain/
|       |   `-- models.py
|       |-- infrastructure/
|       |   |-- browser_fetcher.py
|       |   |-- config.py
|       |   |-- health.py
|       |   |-- healthcheck.py
|       |   |-- metrics.py
|       |   |-- rate_limiter.py
|       |   |-- persistence/
|       |   `-- sources/
|       |       |-- kufar/
|       |       `-- realt/
|       `-- interfaces/
|           |-- telegram/
|           `-- worker/
`-- tests/
```

## Использование бота

1. Откройте Telegram-бота и отправьте `/start` или `/menu`.
2. Создайте новый поиск.
3. Настройте фильтры: тип жилья, район, метро, комнаты, цену, ключевые и
   исключающие слова.
4. Включите слежение.
5. При включении слежения бот запомнит текущую выдачу. После этого worker будет
   присылать новые объявления, опубликованные после включения слежения.
6. Используйте историю, избранное и список заблокированных продавцов через
   inline-меню бота.

Доступные команды:

| Команда | Назначение |
| --- | --- |
| `/start` | Создать дефолтный профиль и открыть главное меню. |
| `/menu` | Открыть главное меню. |
| `/settings` | Показать сохранённые поиски. |
| `/status` | Показать chat id, количество поисков и интервал проверки. |
