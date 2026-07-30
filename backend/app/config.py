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
    redis_url: str = "redis://redis:6379/0"
    celery_worker_concurrency: int = Field(default=2, ge=1, le=32)
    task_soft_time_limit_seconds: int = Field(default=45, ge=10, le=300)
    task_hard_time_limit_seconds: int = Field(default=60, ge=15, le=360)
    task_max_retries: int = Field(default=3, ge=0, le=9)
    domain_lock_ttl_seconds: int = Field(default=90, ge=30, le=600)
    max_active_jobs: int = Field(default=10000, ge=10, le=1000000)
    enqueue_rate_limit: int = Field(default=20, ge=1, le=1000)
    enqueue_rate_window_seconds: int = Field(default=60, ge=10, le=3600)
    bulk_enqueue_limit: int = Field(default=1000, ge=1, le=10000)
    browser_redis_url: str = "redis://redis:6379/1"
    browser_worker_concurrency: int = Field(default=1, ge=1, le=4)
    browser_contexts_per_worker: int = Field(default=1, ge=1, le=4)
    browser_navigation_timeout_seconds: int = Field(default=15, ge=2, le=60)
    browser_task_soft_time_limit_seconds: int = Field(default=40, ge=10, le=180)
    browser_task_hard_time_limit_seconds: int = Field(default=50, ge=15, le=240)
    browser_max_redirects: int = Field(default=5, ge=0, le=10)
    browser_max_requests: int = Field(default=100, ge=5, le=1000)
    browser_max_transferred_bytes: int = Field(default=5_000_000, ge=1024, le=50_000_000)
    browser_max_text_characters: int = Field(default=50_000, ge=1000, le=200_000)
    browser_max_headings: int = Field(default=100, ge=1, le=1000)
    browser_max_links: int = Field(default=250, ge=1, le=2000)
    browser_max_buttons: int = Field(default=100, ge=1, le=1000)
    browser_screenshot_enabled: bool = False
    browser_screenshot_width: int = Field(default=1280, ge=320, le=2560)
    browser_screenshot_height: int = Field(default=720, ge=240, le=4096)
    browser_screenshot_max_bytes: int = Field(default=2_000_000, ge=1024, le=10_000_000)
    browser_artifact_root: str = "/artifacts"
    browser_artifact_retention_hours: int = Field(default=24, ge=1, le=720)
    browser_max_retries: int = Field(default=2, ge=0, le=5)
    browser_confidence_threshold: int = Field(default=45, ge=0, le=100)
    browser_min_static_text_characters: int = Field(default=300, ge=0, le=10000)
    browser_enqueue_rate_limit: int = Field(default=5, ge=1, le=100)
    browser_chromium_max_tasks: int = Field(default=25, ge=1, le=1000)
    ai_enabled: bool = False
    ai_redis_url: str = "redis://redis:6379/2"
    ai_provider: str = "disabled"
    ai_model: str = ""
    ai_model_version: str = ""
    ai_api_key: str = ""
    ai_endpoint: str = ""
    ai_timeout_seconds: int = Field(default=20, ge=1, le=120)
    ai_max_retries: int = Field(default=2, ge=0, le=5)
    ai_max_input_characters: int = Field(default=20_000, ge=1000, le=100_000)
    ai_max_output_tokens: int = Field(default=1000, ge=100, le=8000)
    ai_confidence_threshold: float = Field(default=0.75, ge=0, le=1)
    ai_conflict_threshold: float = Field(default=0.10, ge=0, le=1)
    ai_screenshot_enabled: bool = False
    ai_worker_concurrency: int = Field(default=1, ge=1, le=8)
    ai_daily_request_limit: int = Field(default=100, ge=0, le=1_000_000)
    ai_monthly_cost_limit: float = Field(default=0, ge=0)
    ai_requests_per_minute: int = Field(default=10, ge=1, le=1000)
    ai_requests_per_domain: int = Field(default=5, ge=1, le=1000)
    ai_temperature: float = Field(default=0, ge=0, le=1)

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
        if not self.redis_url.startswith(
            ("redis://", "rediss://")
        ) or not self.browser_redis_url.startswith(("redis://", "rediss://")):
            raise ValueError("Redis URLs must use redis:// or rediss://")
        if self.task_soft_time_limit_seconds >= self.task_hard_time_limit_seconds:
            raise ValueError("task soft time limit must be lower than hard time limit")
        if self.domain_lock_ttl_seconds <= self.task_hard_time_limit_seconds:
            raise ValueError("domain lock TTL must exceed the hard task time limit")
        if self.browser_task_soft_time_limit_seconds >= self.browser_task_hard_time_limit_seconds:
            raise ValueError("browser soft time limit must be lower than browser hard time limit")
        if self.domain_lock_ttl_seconds <= self.browser_task_hard_time_limit_seconds:
            raise ValueError("domain lock TTL must exceed the browser hard task time limit")
        if self.ai_enabled:
            if self.ai_provider == "disabled":
                raise ValueError("AI_PROVIDER cannot be disabled when AI_ENABLED is true")
            if not self.ai_model:
                raise ValueError("AI_MODEL is required when AI is enabled")
            if self.ai_provider != "fake" and (not self.ai_api_key or not self.ai_endpoint):
                raise ValueError("AI_API_KEY and AI_ENDPOINT are required for network AI providers")
            if self.ai_endpoint and not self.ai_endpoint.startswith("https://"):
                raise ValueError("AI_ENDPOINT must use HTTPS")
        if not self.ai_redis_url.startswith(("redis://", "rediss://")):
            raise ValueError("AI_REDIS_URL must use redis:// or rediss://")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
