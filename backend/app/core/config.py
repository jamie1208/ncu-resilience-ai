"""應用程式設定：只從環境變數與 `.env` 讀取，不寫死任何金鑰。

`.env` 讀取順序：repo 根目錄的 `.env`（與 Docker Compose 共用），再來是目前目錄的 `.env`。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

Environment = Literal["development", "test", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

_REPO_ENV = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    """全域設定。新增金鑰類欄位時使用 `SecretStr`，範例值只放 `.env.example`。"""

    model_config = SettingsConfigDict(
        env_prefix="NCU_",
        env_file=(_REPO_ENV, ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "ncu-resilience-ai"
    version: str = "0.1.0"
    environment: Environment = "development"
    log_level: LogLevel = "INFO"
    api_prefix: str = "/api/v1"

    # 資料庫：與 Docker Compose 共用 POSTGRES_*；容器內以 NCU_DB_HOST=db 覆寫主機
    db_user: str | None = Field(None, validation_alias=AliasChoices("NCU_DB_USER", "POSTGRES_USER"))
    db_password: SecretStr | None = Field(
        None, validation_alias=AliasChoices("NCU_DB_PASSWORD", "POSTGRES_PASSWORD")
    )
    db_name: str | None = Field(None, validation_alias=AliasChoices("NCU_DB_NAME", "POSTGRES_DB"))
    db_host: str = Field("localhost", validation_alias=AliasChoices("NCU_DB_HOST"))
    db_port: int = Field(5432, validation_alias=AliasChoices("NCU_DB_PORT", "POSTGRES_PORT"))

    # Celery broker（P1-T07）：容器內為 redis://redis:6379/0，見 .env.example
    redis_url: str = "redis://localhost:6379/0"

    @property
    def database_url(self) -> URL:
        """asyncpg 連線 URL；缺少帳密或資料庫名稱時拋出 ValueError。"""
        missing = [
            name
            for name, value in (
                ("POSTGRES_USER", self.db_user),
                ("POSTGRES_PASSWORD", self.db_password),
                ("POSTGRES_DB", self.db_name),
            )
            if not value
        ]
        if missing or self.db_password is None:
            raise ValueError(f"資料庫設定不完整，請在 .env 設定：{', '.join(missing)}")
        return URL.create(
            "postgresql+asyncpg",
            username=self.db_user,
            password=self.db_password.get_secret_value(),
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        )


@lru_cache
def get_settings() -> Settings:
    """回傳快取的設定實例（FastAPI 依賴注入使用）。"""
    return Settings()
