"""
Application settings using Pydantic v2 Settings management.
All configuration is driven by environment variables with sensible defaults.
"""
from functools import lru_cache
from typing import List, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Central settings object.
    Values are loaded from environment variables (case-insensitive),
    then from .env file, then from defaults.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- API ----
    api_host: str = Field(default="0.0.0.0", description="Host to bind the API server")
    api_port: int = Field(default=8000, ge=1, le=65535)
    api_key: str = Field(
        default="dev-secret-key-change-in-production",
        description="API key for protected endpoints",
    )
    cors_origins: str = Field(
        default="http://localhost:3000,http://localhost:8080",
        description="Comma-separated CORS allowed origins",
    )

    # ---- Database ----
    database_url: str = Field(
        default="sqlite+aiosqlite:///./jobs.db",
        description="SQLAlchemy async database URL",
    )

    # ---- Scraper ----
    scrape_interval_minutes: int = Field(default=60, ge=1)
    max_concurrent_scrapers: int = Field(default=3, ge=1, le=10)
    request_timeout_seconds: int = Field(default=30, ge=5)
    min_delay_seconds: float = Field(default=2.0, ge=0.5)
    max_delay_seconds: float = Field(default=8.0, ge=1.0)

    # ---- Proxy ----
    proxy_list: str = Field(
        default="",
        description="Comma-separated proxy URLs (http:// or socks5://)",
    )

    # ---- User Agent ----
    ua_pool_size: int = Field(default=50, ge=10)
    ua_chrome_weight: int = Field(default=65, ge=0)
    ua_firefox_weight: int = Field(default=20, ge=0)
    ua_safari_weight: int = Field(default=10, ge=0)
    ua_edge_weight: int = Field(default=5, ge=0)

    # ---- Logging ----
    log_level: str = Field(default="INFO")
    log_format: str = Field(default="console", pattern="^(json|console)$")

    # ---- Feature Flags ----
    enable_metrics: bool = Field(default=True)
    enable_scheduler: bool = Field(default=True)

    # ---- Rate Limiting ----
    rate_limit_requests: int = Field(default=100, ge=1)
    rate_limit_window_seconds: int = Field(default=60, ge=1)

    # ---- Sources ----
    enabled_sources: str = Field(
        default="remoteok,hn_jobs,indeed_rss",
        description="Comma-separated list of enabled scraper sources",
    )

    # ---- Indeed RSS ----
    indeed_default_query: str = Field(default="software engineer")
    indeed_default_location: str = Field(default="remote")

    # ---- HN Jobs ----
    hn_jobs_max_results: int = Field(default=50, ge=1, le=500)

    # ---- RemoteOK ----
    remoteok_max_results: int = Field(default=100, ge=1, le=500)

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in valid:
            raise ValueError(f"log_level must be one of {valid}")
        return upper

    @property
    def proxy_urls(self) -> List[str]:
        """Parse proxy_list string into a list of proxy URLs."""
        if not self.proxy_list:
            return []
        return [p.strip() for p in self.proxy_list.split(",") if p.strip()]

    @property
    def cors_origins_list(self) -> List[str]:
        """Parse CORS origins string into a list."""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def enabled_sources_list(self) -> List[str]:
        """Parse enabled sources string into a list."""
        return [s.strip() for s in self.enabled_sources.split(",") if s.strip()]

    @property
    def is_postgres(self) -> bool:
        """Check if using PostgreSQL."""
        return self.database_url.startswith("postgresql")

    @property
    def is_sqlite(self) -> bool:
        """Check if using SQLite."""
        return self.database_url.startswith("sqlite")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Return cached settings instance.
    Uses lru_cache so the .env file is only read once.
    """
    return Settings()
