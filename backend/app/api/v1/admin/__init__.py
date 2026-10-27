"""管理端 API（/api/v1/admin/*）：全部需要登入，各端點再檢查權限。"""

from fastapi import APIRouter, Depends

from app.api.deps import require_login
from app.api.v1.admin import (
    actions,
    alerts,
    audit_logs,
    dashboard,
    events,
    ingestion_runs,
    recurrences,
    skills,
)

# router 層先擋未登入，避免新端點忘了加權限依賴就變成免登入
router = APIRouter(prefix="/admin", dependencies=[Depends(require_login)])
router.include_router(events.router)
router.include_router(alerts.router)
router.include_router(dashboard.router)
router.include_router(audit_logs.router)
router.include_router(ingestion_runs.router)
router.include_router(skills.router)
router.include_router(actions.router)
router.include_router(actions.event_router)
router.include_router(recurrences.router)

__all__ = ["router"]
