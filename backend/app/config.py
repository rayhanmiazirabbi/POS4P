from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="PHARMACY_", extra="ignore")

    environment: str = "development"
    database_url: str = "postgresql+asyncpg://pharmacy:pharmacy@localhost:5432/pharmacy"
    secret_key: str = "development-only-secret-change-me"
    access_token_minutes: int = 15
    refresh_token_days: int = 30
    otp_expiry_seconds: int = 300
    otp_max_attempts: int = 5
    otp_request_window_seconds: int = 900
    otp_max_requests_per_window: int = 3
    pin_max_attempts: int = 5
    pin_lockout_seconds: int = 900
    billing_webhook_secret: str = "development-only-billing-webhook-secret"
    billing_grace_period_days: int = 7
    billing_trial_days: int = 14
    billing_period_days: int = 30
    supplier_invite_ttl_days: int = 14
    audit_signing_secret: str = "development-only-audit-signing-secret"
    audit_retention_days: int = 3650


@lru_cache
def get_settings() -> Settings:
    return Settings()
