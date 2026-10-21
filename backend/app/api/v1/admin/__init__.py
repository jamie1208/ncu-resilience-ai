"""管理端 API（/api/v1/admin/*）：全部需要管理者權限。"""

from fastapi import APIRouter

from app.api.v1.admin import events, ingestion_runs

router = APIRouter(prefix="/admin")
router.include_router(events.router)
router.include_router(ingestion_runs.router)

__all__ = ["router"]
