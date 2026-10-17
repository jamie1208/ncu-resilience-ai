"""應用程式設定：只從環境變數（前綴 `NCU_`）與本機 `.env` 讀取，不寫死任何金鑰。"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "test", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    """全域設定。新增金鑰類欄位時使用 `SecretStr`，範例值只放 `.env.example`。"""

    model_config = SettingsConfigDict(
        env_prefix="NCU_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "ncu-resilience-ai"
    version: str = "0.1.0"
    environment: Environment = "development"
    log_level: LogLevel = "INFO"
    api_prefix: str = "/api/v1"


@lru_cache
def get_settings() -> Settings:
    """回傳快取的設定實例（FastAPI 依賴注入使用）。"""
    return Settings()
