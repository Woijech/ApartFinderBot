"""Application configuration loaded from environment and ``.env``."""

from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for ApartmentFinder with typed environment validation."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="APARTMENTFINDER_",
        extra="ignore",
        populate_by_name=True,
    )

    kufar_base_url: str = "https://re.kufar.by"
    realt_base_url: str = "https://realt.by"
    timeout_seconds: float = Field(default=20, gt=0)
    request_retries: int = Field(default=2, ge=0)
    request_retry_delay_seconds: float = Field(default=2, ge=0)
    log_level: str = "INFO"
    user_agent: str = "ApartmentFinder/0.1 (+local research parser)"
    telegram_bot_token: SecretStr | None = Field(
        default=None,
        validation_alias="TELEGRAM_BOT_TOKEN",
    )
    database_url: str = (
        "postgresql+psycopg://apartmentfinder:apartmentfinder"
        "@localhost:5432/apartmentfinder"
    )
    seen_ttl_days: int = Field(default=60, ge=1)
    max_seen_per_chat: int = Field(default=5000, ge=1)
    bot_poll_interval_seconds: float = Field(default=300, gt=0)
    bot_initial_poll_delay_seconds: float = Field(default=10, ge=0)
    bot_max_notifications_per_check: int = Field(default=5, ge=0)
    bot_fetch_timeout_seconds: float = Field(default=8, gt=0)
    bot_fetch_retries: int = Field(default=1, ge=0)
    bot_fetch_retry_delay_seconds: float = Field(default=1, ge=0)
    bot_display_timezone: str = "Europe/Minsk"
    allowed_chat_ids: str = ""
    bot_max_pages: int = Field(default=1, ge=1)
    bot_page_delay_seconds: float = Field(default=1, ge=0)
    bot_max_images: int = Field(default=3, ge=0, le=10)
    browser_fetch_enabled: bool = False
    browser_fetch_cdp_url: str = "http://cloakbrowser:9222"
    browser_fetch_timeout_seconds: float = Field(default=20, gt=0)
    browser_fetch_wait_until: str = "networkidle"
    browser_fetch_fallback_on_empty: bool = True
    browser_fetch_fingerprint_seed: str | None = None

    @field_validator(
        "kufar_base_url",
        "realt_base_url",
        "user_agent",
        "database_url",
        "bot_display_timezone",
        "browser_fetch_cdp_url",
        "browser_fetch_wait_until",
    )
    @classmethod
    def validate_not_blank(cls, value: str) -> str:
        """Reject blank string values that would fail later at runtime."""
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("bot_display_timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        """Validate timezone names at startup instead of during formatting."""
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError(f"unknown timezone: {value}") from error
        return value

    @field_validator("database_url")
    @classmethod
    def validate_postgres_url(cls, value: str) -> str:
        """Keep runtime storage on PostgreSQL only."""
        if not value.startswith(("postgresql://", "postgresql+psycopg://")):
            raise ValueError("database_url must be a PostgreSQL SQLAlchemy URL")
        return value

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        """Normalize and validate the configured application log level."""
        normalized = value.upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
            raise ValueError("log_level must be DEBUG, INFO, WARNING, or ERROR")
        return normalized

    @field_validator("browser_fetch_cdp_url")
    @classmethod
    def validate_browser_fetch_cdp_url(cls, value: str) -> str:
        """Validate the CDP endpoint used by the optional browser fallback."""
        if not value.startswith(("http://", "https://")):
            raise ValueError("browser_fetch_cdp_url must start with http:// or https://")
        return value.rstrip("/")

    @field_validator("browser_fetch_wait_until")
    @classmethod
    def validate_browser_fetch_wait_until(cls, value: str) -> str:
        """Validate Playwright wait modes accepted by page.goto."""
        if value not in {"commit", "domcontentloaded", "load", "networkidle"}:
            raise ValueError(
                "browser_fetch_wait_until must be commit, domcontentloaded, "
                "load, or networkidle"
            )
        return value

    @field_validator("browser_fetch_fingerprint_seed", mode="before")
    @classmethod
    def empty_browser_fingerprint_seed_to_none(cls, value: object) -> object:
        """Allow disabling fingerprint scoping with an empty env value."""
        if value == "":
            return None
        return value

    @field_validator("allowed_chat_ids")
    @classmethod
    def validate_allowed_chat_ids(cls, value: str) -> str:
        """Validate comma-separated chat ids without forcing JSON syntax."""
        if not value.strip():
            return ""
        for item in value.split(","):
            int(item.strip())
        return value

    @property
    def allowed_chat_id_set(self) -> set[int]:
        """Return Telegram chat ids allowed to use a private bot."""
        if not self.allowed_chat_ids.strip():
            return set()
        return {int(item.strip()) for item in self.allowed_chat_ids.split(",")}

    @property
    def telegram_bot_token_value(self) -> str | None:
        """Return the plain bot token only at the Telegram API boundary."""
        if self.telegram_bot_token is None:
            return None
        return self.telegram_bot_token.get_secret_value()


settings = Settings()
