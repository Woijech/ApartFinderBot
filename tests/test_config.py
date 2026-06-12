import pytest
from pydantic import ValidationError

from apartmentfinder.infrastructure.config import Settings, SourceLimitSettings


def test_settings_keeps_telegram_token_secret() -> None:
    settings = Settings(telegram_bot_token="123:secret-token", _env_file=None)

    assert settings.telegram_bot_token_value == "123:secret-token"
    assert "secret-token" not in repr(settings)


def test_settings_rejects_invalid_numeric_values() -> None:
    with pytest.raises(ValidationError):
        Settings(timeout_seconds=0, _env_file=None)


def test_settings_rejects_unknown_timezone() -> None:
    with pytest.raises(ValidationError):
        Settings(bot_display_timezone="Mars/Olympus", _env_file=None)


def test_settings_parses_allowed_chat_ids() -> None:
    settings = Settings(allowed_chat_ids="123, 456", _env_file=None)

    assert settings.allowed_chat_id_set == {123, 456}


def test_settings_rejects_sqlite_database_url() -> None:
    with pytest.raises(ValidationError):
        Settings(database_url="sqlite:///data/apartmentfinder.sqlite3", _env_file=None)


def test_settings_reads_new_apartmentfinder_env_prefix(monkeypatch) -> None:
    monkeypatch.setenv("APARTMENTFINDER_BOT_MAX_IMAGES", "4")

    settings = Settings(_env_file=None)

    assert settings.bot_max_images == 4


def test_settings_defaults_to_nine_listing_images() -> None:
    settings = Settings(_env_file=None)

    assert settings.bot_max_images == 9


def test_settings_reads_source_base_urls(monkeypatch) -> None:
    monkeypatch.setenv("APARTMENTFINDER_KUFAR_BASE_URL", "https://k.example.test")
    monkeypatch.setenv("APARTMENTFINDER_REALT_BASE_URL", "https://r.example.test")

    settings = Settings(_env_file=None)

    assert settings.kufar_base_url == "https://k.example.test"
    assert settings.realt_base_url == "https://r.example.test"


def test_settings_accepts_log_level_values() -> None:
    assert Settings(log_level="INFO", _env_file=None).log_level == "INFO"
    assert Settings(log_level="debug", _env_file=None).log_level == "DEBUG"


def test_settings_rejects_invalid_log_level() -> None:
    with pytest.raises(ValidationError, match="log_level must be"):
        Settings(log_level="verbose", _env_file=None)


def test_settings_parses_browser_fetch_bool_from_string() -> None:
    settings = Settings(browser_fetch_enabled="true", _env_file=None)

    assert settings.browser_fetch_enabled is True


def test_settings_rejects_invalid_browser_fetch_timeout() -> None:
    with pytest.raises(ValidationError):
        Settings(browser_fetch_timeout_seconds=0, _env_file=None)


def test_settings_reads_healthcheck_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.health_host == "0.0.0.0"
    assert settings.bot_health_port == 8080
    assert settings.worker_health_port == 8081
    assert settings.readiness_poll_max_age_seconds == 900
    assert settings.source_fetch_concurrency == 2
    assert settings.subscription_check_concurrency == 3


def test_settings_rejects_invalid_health_port() -> None:
    with pytest.raises(ValidationError):
        Settings(bot_health_port=0, _env_file=None)


def test_settings_rejects_invalid_concurrency() -> None:
    with pytest.raises(ValidationError):
        Settings(source_fetch_concurrency=0, _env_file=None)


def test_settings_reads_source_limit_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.source_limit("kufar").max_requests_per_minute == 60
    assert settings.source_limit("realt").browser_fallback_limit == 3
    assert settings.source_limit("unknown") == SourceLimitSettings()


def test_settings_reads_nested_source_limit_env(monkeypatch) -> None:
    monkeypatch.setenv(
        "APARTMENTFINDER_SOURCE_LIMITS__KUFAR__MAX_REQUESTS_PER_MINUTE",
        "12",
    )
    monkeypatch.setenv("APARTMENTFINDER_SOURCE_LIMITS__KUFAR__MIN_DELAY", "1.5")

    settings = Settings(_env_file=None)

    assert settings.source_limit("kufar").max_requests_per_minute == 12
    assert settings.source_limit("kufar").min_delay == 1.5
