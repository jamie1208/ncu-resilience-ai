"""健康檢查端點：GET /api/v1/health。

P0 只回報應用程式本身；資料庫與 Redis 檢查於 P0-T04／P0-T05 加入。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.api.responses import envelope
from app.core.config import Settings, get_settings

router = APIRouter(tags=["health"])


@router.get("/health")
def health(settings: Annotated[Settings, Depends(get_settings)]) -> dict[str, Any]:
    """回傳服務狀態、名稱、版本與環境。"""
    return envelope(
        {
            "status": "ok",
            "service": settings.app_name,
            "version": settings.version,
            "environment": settings.environment,
        }
    )
