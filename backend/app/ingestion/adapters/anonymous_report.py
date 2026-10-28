"""匿名回報 Adapter（P5-T13），來源類型 `anonymous_report`。

公開端點 `POST /api/v1/public/reports` 把投稿寫進 `anonymous_reports` 收件匣；這個 Adapter 依
遞增 id 把新投稿轉成 `RawItem`，由 worker 照一般來源的流程標準化、去重、寫入並排入分析
（ADR-0015 第 8 點）。

- 另一個獨立來源：來源代碼不是 `ncu-*`，查證規則不會把它當成官方來源；公開規則也不列出它的
  名稱或內容（`app/publishing/policy.py`），所以投稿原文永不公開。
- 不可信內容：`RawItem` 的內容和其他來源一樣，送進模型前由 `app/ai/runner/prompt.py` 包在
  `<untrusted_source>` 中並跳脫標籤，投稿無法改變分析指令。
- 內容只有「地點：…」（有填時）與投稿本文；沒有標題與發布時間，擷取時間為收到時間。
- 網址：收件匣沒有網址，以 `report_url` 加上 `?id=<收件匣 id>` 作為內部識別（只在管理端出現）。
- cursor 是已處理的最後一個收件匣 id；收件匣資料不刪除。

來源設定（`SourceConfig.options`）：
- `report_url`（必填）：公開網站的回報頁網址，例如 `http://127.0.0.1:8080/report`
- `max_items`：每輪最多讀幾則（1–1000，預設 200）
"""

from __future__ import annotations

from collections.abc import Sequence
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.ingestion.registry import registry
from app.ingestion.types import RawItem, SourceConfig
from app.models.anonymous_report import AnonymousReport

SOURCE_TAG = "匿名回報"
EXTERNAL_ID_PREFIX = "report-"


class AnonymousReportOptions(BaseModel):
    """匿名回報來源的設定；無效值在建立 Adapter 時拋出 ValueError。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    report_url: str
    max_items: int = Field(default=200, ge=1, le=1000)

    @field_validator("report_url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.netloc or parts.query:
            raise ValueError("report_url 必須是沒有查詢字串的 http(s) 絕對網址")
        return value


def _report_id(item: RawItem) -> int:
    return int((item.external_id or "").removeprefix(EXTERNAL_ID_PREFIX))


@registry.register("anonymous_report")
class AnonymousReportAdapter:
    """從 `anonymous_reports` 收件匣讀取新投稿。"""

    def __init__(
        self,
        config: SourceConfig,
        *,
        maker: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self.code = config.code
        self.options = AnonymousReportOptions.model_validate(dict(config.options))
        self._maker = maker

    async def fetch_since(self, cursor: str | None) -> list[RawItem]:
        """取得 id 大於 cursor 的投稿（依 id 遞增）。"""
        after = int(cursor) if cursor else 0
        stmt = (
            select(AnonymousReport)
            .where(AnonymousReport.id > after)
            .order_by(AnonymousReport.id)
            .limit(self.options.max_items)
        )
        if self._maker is not None:
            async with self._maker() as session:
                rows = list((await session.execute(stmt)).scalars())
        else:
            engine = create_async_engine(get_settings().database_url)
            try:
                async with async_sessionmaker(engine)() as session:
                    rows = list((await session.execute(stmt)).scalars())
            finally:
                await engine.dispose()
        return [self._to_item(row) for row in rows]

    def next_cursor(self, items: Sequence[RawItem], previous: str | None) -> str | None:
        """本輪最大的收件匣 id；沒有新投稿時沿用 previous。"""
        if not items:
            return previous
        return str(max(_report_id(item) for item in items))

    def _to_item(self, row: AnonymousReport) -> RawItem:
        content = row.content if row.location is None else f"地點：{row.location}\n\n{row.content}"
        return RawItem(
            source_code=self.code,
            external_id=f"{EXTERNAL_ID_PREFIX}{row.id}",
            url=f"{self.options.report_url}?id={row.id}",
            content=content,
            content_type="text/plain",
            fetched_at=row.submitted_at,
            tags=frozenset({SOURCE_TAG}),
        )
