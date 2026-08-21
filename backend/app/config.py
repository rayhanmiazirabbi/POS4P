from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="PHARMACY_", extra="ignore")

    environment: str = "development"
    database_url: str = "postgresql+asyncpg://pharmacy:pharmacy@localhost:5432/pharmacy"
    access_token_minutes: int = 15
    refresh_token_days: int = 30
    otp_expiry_seconds: int = 300


@lru_cache
def get_settings() -> Settings:
    return Settings()
