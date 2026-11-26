from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, case_sensitive=False, extra="ignore")

    app_env: str = "development"
    database_url: str = Field(min_length=1)
    session_signing_key: SecretStr
    mega_ott_api_base: str = "https://megaott.net/api"
    mega_ott_api_token: SecretStr | None = None
    session_ttl_seconds: int = Field(default=300, ge=30, le=300)
    # Secret configured privately on the backend; no hardcoded default.
    entitlement_monitor_key: SecretStr | None = None
    # Disabled until the complete native Jellyfin transport is certified.
    private_hls_enabled: bool = False
    # Explicit compatibility for HTTP origins assigned by the authenticated Mega API.
    # Credentials and media on that upstream hop are NOT encrypted. Never a client option.
    private_media_allow_authorized_http: bool = False
    # Explicit server policy; never selected after an upstream denial.
    private_live_input_format: Literal["m3u8", "ts"] = "m3u8"
    private_media_key: SecretStr | None = None
    auth_rate_window_seconds: int = Field(default=60, ge=10, le=3600)
    auth_rate_per_username: int = Field(default=10, ge=1, le=100)
    auth_rate_global: int = Field(default=120, ge=10, le=10000)
    # Browsing hundreds of categories must not consume the *login* budget.
    # Both budgets remain PostgreSQL-backed and rate-limited per identity.
    catalog_rate_per_username: int = Field(default=180, ge=20, le=1000)
    catalog_rate_global: int = Field(default=2400, ge=100, le=20000)

    @field_validator("mega_ott_api_base")
    @classmethod
    def mega_base_must_be_https(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme.lower() != "https" or not parsed.hostname:
            raise ValueError("MEGA_OTT_API_BASE must be an HTTPS URL with a hostname")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("MEGA_OTT_API_BASE must not contain credentials, query, or fragment")
        return value.rstrip("/")

    @model_validator(mode="after")
    def reject_unsafe_non_test_defaults(self) -> "Settings":
        if self.app_env.casefold() != "test":
            signing_key = self.session_signing_key.get_secret_value().strip()
            if not signing_key or signing_key == "change-me":
                raise ValueError("SESSION_SIGNING_KEY must be set to a non-default value")
            if len(signing_key.encode()) < 32:
                raise ValueError("SESSION_SIGNING_KEY must be at least 32 bytes")
        if self.private_live_input_format == "ts" and not self.private_hls_enabled:
            raise ValueError("Continuous TS requires private media routes")
        if self.private_media_allow_authorized_http and not self.private_hls_enabled:
            raise ValueError("Authorized HTTP media requires private media routes")
        if self.private_hls_enabled:
            key = self.private_media_key.get_secret_value() if self.private_media_key else ""
            monitor = self.entitlement_monitor_key
            if (not 32 <= len(key.encode()) <= 1024 or key != key.strip()
                    or key == self.session_signing_key.get_secret_value()
                    or (monitor is not None and key == monitor.get_secret_value())):
                raise ValueError("Private HLS requires an independent private media key")
        return self

    def require_mega_token(self) -> str:
        if self.mega_ott_api_token is None:
            raise RuntimeError("Mega integration requires MEGA_OTT_API_TOKEN")
        token = self.mega_ott_api_token.get_secret_value().strip()
        if not token or token == "change-me":
            raise RuntimeError("Mega integration requires a non-default MEGA_OTT_API_TOKEN")
        return token


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]

