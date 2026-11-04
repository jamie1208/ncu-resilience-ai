"""人工確認原因（P7-T07，ADR-0017）。

小報告的原因由 S07 產生，只有官方來源逐字原句支持的才是 confirmed；使用者若已經用其他管道
（例如向權責單位詢問）確認了推測原因，可以在後台把它標為人工確認，也可以回滾到推測：

- 只限最新一份有效小報告中 basis 為 hypothesized 的原因；S07 的判定（`basis`）不改寫；
- 確認依據必填，最多 200 字，含個資時拒絕；回滾不需要依據；
- 決策只新增不修改（`report_cause_decisions`），同一個原因最新的一筆是目前狀態；
  已確認不能重複確認，未確認不能回滾；
- 確認只套用在當時那份小報告：重新產生小報告後原因代號會變，需要重新確認；
- 每次決策寫入稽核 `report.cause_decide`，metadata 只有報告、原因代號與 from／to，不含依據文字。

公開規則（`app/publishing/policy.py`）把人工確認的原因視為 confirmed，依據它的措施在下一次
公開同步時公開；疑似指控與 PII 遮蔽規則不變。

本模組只 flush、不 commit，由呼叫端決定交易邊界；所有檢查都在寫入之前完成。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.runner.validate import find_pii
from app.core.security import Principal
from app.models.report import EventReport, ReportCauseDecision
from app.reports.service import latest_report
from app.services.audit import record_audit

BASIS_MAX = 200


class CauseDecision(StrEnum):
    """原因的人工決策；值存入資料庫，已上線的值不可改名。"""

    CONFIRMED = "confirmed"  # 人工確認
    HYPOTHESIZED = "hypothesized"  # 回滾到推測


class CauseDecisionError(Exception):
    """人工確認原因失敗；由 API 轉成對應的錯誤碼。"""


class CauseNotFoundError(CauseDecisionError):
    """事件、報告或原因不存在。"""


class CauseConflictError(CauseDecisionError):
    """報告不是最新有效的、原因不是推測，或與目前決策衝突。"""


class CauseInvalidError(CauseDecisionError):
    """輸入不合格；`field` 為出問題的欄位。"""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field


def _basis(value: str | None) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        raise CauseInvalidError("basis", "確認原因必須填寫確認依據")
    if len(cleaned) > BASIS_MAX:
        raise CauseInvalidError("basis", f"確認依據超過 {BASIS_MAX} 字")
    if find_pii(cleaned):
        raise CauseInvalidError("basis", "確認依據疑似含個資")
    return cleaned


async def current_decisions(
    session: AsyncSession, report_id: uuid.UUID
) -> dict[str, ReportCauseDecision]:
    """報告中各原因目前的人工決策（最新一筆）；沒有決策的原因不在結果中。"""
    rows = await session.scalars(
        select(ReportCauseDecision)
        .where(ReportCauseDecision.report_id == report_id)
        .order_by(ReportCauseDecision.decided_at, ReportCauseDecision.id)
    )
    latest: dict[str, ReportCauseDecision] = {}
    for row in rows:
        latest[row.cause_id] = row  # 由舊到新覆蓋，留下最新的一筆
    return latest


async def confirmed_cause_ids(session: AsyncSession, report_id: uuid.UUID) -> frozenset[str]:
    """報告中目前經人工確認的原因代號。"""
    decisions = await current_decisions(session, report_id)
    return frozenset(c for c, d in decisions.items() if d.decision == CauseDecision.CONFIRMED)


async def _target_report(
    session: AsyncSession, event_id: uuid.UUID, report_id: uuid.UUID
) -> EventReport:
    report = await session.get(EventReport, report_id)
    if report is None or report.event_id != event_id:
        raise CauseNotFoundError("找不到事件小報告")
    latest = await latest_report(session, event_id)
    if latest is None or latest.id != report.id or report.invalidated_at is not None:
        raise CauseConflictError("只能確認最新一份有效小報告的原因，請重新整理後再操作")
    return report


async def decide_cause(
    session: AsyncSession,
    principal: Principal,
    event_id: uuid.UUID,
    report_id: uuid.UUID,
    cause_id: str,
    to: CauseDecision,
    *,
    basis: str | None = None,
) -> ReportCauseDecision:
    """人工確認（confirmed）或回滾（hypothesized）推測原因；違反規則時拋出 CauseDecisionError。"""
    report = await _target_report(session, event_id, report_id)
    cause = next((c for c in report.causes if str(c.get("cause_id")) == cause_id), None)
    if cause is None:
        raise CauseNotFoundError("找不到原因")
    if cause.get("basis") != "hypothesized":
        raise CauseConflictError("只有推測原因需要人工確認")
    note = _basis(basis) if to is CauseDecision.CONFIRMED else None
    current = (await current_decisions(session, report.id)).get(cause_id)
    previous = CauseDecision(current.decision) if current else CauseDecision.HYPOTHESIZED
    if previous is to:
        raise CauseConflictError(
            "原因已經人工確認" if to is CauseDecision.CONFIRMED else "原因尚未人工確認，不需回滾"
        )
    decision = ReportCauseDecision(
        report_id=report.id,
        cause_id=cause_id,
        decision=to.value,
        basis=note,
        decided_by=principal.actor,
        # 不用資料庫的 now()（交易開始時間）：同一個交易內的多次決策才能依時間排序
        decided_at=datetime.now(UTC),
    )
    session.add(decision)
    await session.flush()
    await record_audit(
        session,
        principal,
        "report.cause_decide",
        target=f"event:{event_id}",
        metadata={
            "report_id": str(report.id),
            "cause_id": cause_id,
            "from": previous.value,
            "to": to.value,
        },
    )
    return decision


__all__ = [
    "BASIS_MAX",
    "CauseConflictError",
    "CauseDecision",
    "CauseDecisionError",
    "CauseInvalidError",
    "CauseNotFoundError",
    "confirmed_cause_ids",
    "current_decisions",
    "decide_cause",
]
