from functools import lru_cache

from pydantic import EmailStr, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    database_url: str
    secret_key: str = Field(min_length=32)
    initial_admin_email: EmailStr
    initial_admin_password: str = Field(min_length=16)
    allowed_origins: list[str]
    allowed_hosts: list[str]
    cookie_secure: bool = False
    log_level: str = "INFO"
    session_ttl_minutes: int = Field(default=480, ge=5, le=10080)
    inspector_connect_timeout_seconds: float = Field(default=5.0, ge=0.5, le=30)
    inspector_response_timeout_seconds: float = Field(default=10.0, ge=1, le=60)
    inspector_max_response_bytes: int = Field(default=1_000_000, ge=1024, le=5_000_000)
    inspector_max_text_characters: int = Field(default=50_000, ge=1000, le=200_000)
    inspector_max_redirects: int = Field(default=5, ge=0, le=10)

    @field_validator("allowed_origins", "allowed_hosts", mode="before")
    @classmethod
    def split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("secret_key", "initial_admin_password")
    @classmethod
    def reject_placeholders(cls, value: str) -> str:
        if "change_me" in value.lower():
            raise ValueError("placeholder secrets are not allowed")
        return value

    @model_validator(mode="after")
    def validate_urls(self) -> "Settings":
        if not self.database_url.startswith(("postgresql+asyncpg://", "sqlite+aiosqlite://")):
            raise ValueError("DATABASE_URL must use an async SQLAlchemy driver")
        if not self.allowed_origins or not self.allowed_hosts:
            raise ValueError("origin and host allowlists cannot be empty")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
