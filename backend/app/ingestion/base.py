"""Source Adapter 介面與錯誤類型（規格 F01：來源 Adapter、增量取得、失敗重試）。

每個來源類型（RSS、校內公告 HTML、人工匯入…）實作一個 Adapter。Adapter 只負責
「從來源取得原始項目」；標準化、去重、寫入與重試由 normalize／store／worker 處理。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from app.ingestion.types import RawItem


@runtime_checkable
class SourceAdapter(Protocol):
    """來源 Adapter。

    cursor 是 Adapter 自行定義的不透明字串（例如最後一篇的發布時間或 ID），
    由排程器保存並在下一輪傳回，以達成增量擷取。
    """

    code: str

    async def fetch_since(self, cursor: str | None) -> list[RawItem]:
        """取得 cursor 之後的新項目；cursor 為 None 時取得目前可見的全部項目。"""
        ...

    def next_cursor(self, items: Sequence[RawItem], previous: str | None) -> str | None:
        """依本輪取得的項目計算下一輪的 cursor；沒有新項目時回傳 previous。"""
        ...


class AdapterError(Exception):
    """Adapter 擷取失敗。`retryable` 表示排程器是否應重試（P1-T07）。"""

    retryable: bool = False


class SourceUnavailableError(AdapterError):
    """來源暫時無法取得（逾時、5xx、連線失敗），可重試。"""

    retryable = True


class LayoutChangedError(AdapterError):
    """來源版型改變、必要欄位抽不到（規格 §16）；重試無效，需人工處理。"""

    retryable = False
