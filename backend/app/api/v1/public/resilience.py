"""公開版校園韌性成績單（P6-T05，ADR-0015；使用者 2026-10-28 同意新增此公開端點）。

- `GET /api/v1/public/resilience`：只統計目前以「事件內容」公開中的事件（已查證），時間窗固定
  180 天、不接受參數；只回傳彙總分數、樣本量、覆蓋率、時間窗與限制說明，不含事件、地點、
  措施或任何 ID。資料不足的構面與總分為 null（N/A）。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.api.responses import envelope
from app.api.v1.public.route import PublicRoute
from app.resilience.cri import build_report, report_to_dict

router = APIRouter(tags=["public"], route_class=PublicRoute)

Session = Annotated[AsyncSession, Depends(get_db_session)]


@router.get("/resilience")
async def resilience(session: Session) -> dict[str, Any]:
    """公開版韌性成績單。"""
    report = await build_report(session, published_only=True)
    return envelope(report_to_dict(report))
