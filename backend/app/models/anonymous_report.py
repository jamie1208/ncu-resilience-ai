"""匿名回報收件匣（P5-T13，0014）：anonymous_reports。

公開端點 `POST /api/v1/public/reports` 只寫入這張表；`anonymous_report` Adapter 再由 worker
依 id 遞增拉進既有擷取管線（ADR-0015 第 8 點：投稿是另一個不可信來源）。

刻意只有內容、選填地點與收到時間：不保存 IP、User-Agent、轉送標頭或任何裝置資訊，
也沒有聯絡方式欄位。投稿原文只在管理端可見，永不公開。

P8-T08（0026）：report_receipts 記錄收據碼的 SHA-256 與它對應的投稿（原投稿與補件共用同一個
雜湊）；明文收據碼只在送出的回應中出現一次，不落地。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, CheckConstraint, ForeignKey, Identity, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_col

CONTENT_MIN_CHARS = 10
CONTENT_MAX_CHARS = 2000
LOCATION_MAX_CHARS = 100


class AnonymousReport(Base):
    """一則匿名回報；建立後不修改。"""

    __tablename__ = "anonymous_reports"
    __table_args__ = (
        CheckConstraint(
            f"char_length(content) BETWEEN {CONTENT_MIN_CHARS} AND {CONTENT_MAX_CHARS}",
            name="content_length",
        ),
    )

    # 遞增 id 作為 Adapter 的 cursor；不對外暴露
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    location: Mapped[str | None] = mapped_column(String(LOCATION_MAX_CHARS), nullable=True)
    submitted_at: Mapped[datetime] = created_at_col()


RECEIPT_HASH_CHARS = 64


class ReportReceipt(Base):
    """收據碼雜湊與投稿的對應；一則投稿（原投稿或補件）一列，建立後不修改。"""

    __tablename__ = "report_receipts"

    report_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("anonymous_reports.id", ondelete="CASCADE"), primary_key=True
    )
    # SHA-256（十六進位）；收據碼有 120 位元的隨機性，不需要另外加鹽
    code_hash: Mapped[str] = mapped_column(String(RECEIPT_HASH_CHARS), index=True)
    is_supplement: Mapped[bool] = mapped_column(Boolean, nullable=False)
