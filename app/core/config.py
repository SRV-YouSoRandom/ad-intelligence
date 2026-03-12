"""Application settings via pydantic-settings."""

from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """Central configuration loaded from environment variables."""

    # Meta Ads Library API
    META_ACCESS_TOKEN: str = ""
    META_API_VERSION: str = "v23.0"

    # OpenRouter
    OPENROUTER_API_KEY: str = ""
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    CLASSIFICATION_MODEL: str = "qwen/qwen3-vl-30b-a3b-thinking"
    INSIGHT_MODEL: str = "qwen/qwen3-vl-235b-a22b-thinking"

    # PostgreSQL
    DATABASE_URL: str = "postgresql+asyncpg://adint:adint@postgres:5432/adint"

    # Valkey
    VALKEY_URL: str = "redis://valkey:6379/0"

    # Application
    APP_ENV: str = "production"
    LOG_LEVEL: str = "INFO"
    MEDIA_STORAGE_PATH: str = "/app/media_storage"
    MAX_CONCURRENT_DOWNLOADS: int = 5

    # Rate limiting (Meta API: ~200 calls/hour)
    META_RATE_LIMIT_CALLS: int = 200
    META_RATE_LIMIT_PERIOD: int = 3600  # seconds

    # Frame extraction
    MAX_FRAMES: int = 8
    SCENE_CHANGE_THRESHOLD: float = 0.30

    model_config = {"env_file": ".env", "case_sensitive": True}


settings = Settings()
