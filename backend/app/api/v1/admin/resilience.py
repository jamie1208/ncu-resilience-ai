"""管理端校園韌性成績單 API（P6-T05，規格 §15）。

- `GET /api/v1/admin/resilience`：全部事件的 CRI 報告，附時間窗、樣本量、資料覆蓋率與來源限制；
  `window_days` 可調整（7–730，預設 180）。每次讀取時由 `app/resilience/cri.py` 重算，
  不另存資料表。需要 `reports:read_internal`；只讀，不寫稽核。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session, require_permission
from app.api.responses import envelope
from app.core.security import Permission, Principal
from app.resilience.cri import CriParams, build_report, report_to_dict

router = APIRouter(prefix="/resilience", tags=["admin"])

Session = Annotated[AsyncSession, Depends(get_db_session)]
CanRead = Annotated[Principal, Depends(require_permission(Permission.REPORTS_READ_INTERNAL))]
WindowDays = Annotated[int, Query(ge=7, le=730, description="時間窗天數")]


@router.get("")
async def get_resilience(
    session: Session, _: CanRead, window_days: WindowDays = CriParams.window_days
) -> dict[str, Any]:
    """後台韌性成績單（資料不足的構面分數為 null）。"""
    report = await build_report(session, params=CriParams(window_days=window_days))
    return envelope(report_to_dict(report))
