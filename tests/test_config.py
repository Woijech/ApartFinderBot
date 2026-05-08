import pytest
from pydantic import ValidationError

from kufarpars.config import Settings


def test_settings_parses_preview_bool_from_string() -> None:
    settings = Settings(bot_enable_preview="true", _env_file=None)

    assert settings.bot_enable_preview is True


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


def test_settings_parses_empty_http_proxy_as_none() -> None:
    settings = Settings(http_proxy="", _env_file=None)

    assert settings.http_proxy is None


def test_settings_rejects_invalid_http_proxy_scheme() -> None:
    with pytest.raises(ValidationError):
        Settings(http_proxy="socks5://127.0.0.1:9050", _env_file=None)


def test_settings_rejects_sqlite_database_url() -> None:
    with pytest.raises(ValidationError):
        Settings(database_url="sqlite:///data/kufarpars.sqlite3", _env_file=None)
