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


def test_settings_allows_disabling_legacy_json_migration() -> None:
    settings = Settings(legacy_bot_state_path="", _env_file=None)

    assert settings.legacy_bot_state_path is None


def test_settings_parses_allowed_chat_ids() -> None:
    settings = Settings(allowed_chat_ids="123, 456", _env_file=None)

    assert settings.allowed_chat_ids == {123, 456}
