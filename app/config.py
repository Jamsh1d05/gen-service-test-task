from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://gen:gen@localhost:55432/gen"
    db_pool_size: int = 10
    db_max_overflow: int = 20

    webhook_secret: str = "dev-secret"

    provider_timeout: float = 60.0
    provider_http_timeout: float = 5.0
    provider_urls: dict[str, str] = Field(
        default_factory=lambda: {"alpha": "http://localhost:8001", "beta": "http://localhost:8002"}
    )
    public_base_url: str = "http://localhost:8000"

    watchdog_enabled: bool = True
    watchdog_interval: float = 2.0
    watchdog_batch_size: int = 50

    max_active_tasks: int = 3


@lru_cache
def get_settings() -> Settings:
    return Settings()
