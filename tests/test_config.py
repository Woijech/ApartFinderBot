import pytest
from pydantic import ValidationError

from apartmentfinder.infrastructure.config import Settings


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


