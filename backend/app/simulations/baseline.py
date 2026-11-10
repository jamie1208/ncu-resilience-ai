"""What-if 的歷史基準件數（P8-T09，v1.0 D29）。

依時間窗、類別、代表地點，從 P6-T03 的週件數（`analytics_event_counts_week`，以
`refresh_views` 更新、`load_counts` 讀取、`count_series` 補零）推算「照過去的情況，這段期間
大約會有幾件」：

    基準 = 歷史期間的件數 ÷ 週數 × 時間窗天數 ÷ 7

- 歷史期間：最多 `HISTORY_WEEKS` 個完整週，結束在本週之前（進行中的週不列入）；資料不滿這麼久時
  從最早一筆件數所在的週起算，避免把還沒開始收資料的週當成 0 件；
- 計數規則與 P6-T03 相同（不實、類別 none、被合併的事件不列入，地點依代表地點分組）；
  輸入的地點若是別名，換成代表地點名稱再比對；
- 沒有歷史期間時 `baseline` 為 None，模擬必須手填基準；
- 完全確定性，不呼叫模型；只在管理端使用。件數是「被回報、被彙整成事件」的數量，不等於實際
  發生次數。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.pii import mask_pii
from app.analytics.timeseries import (
    CountRow,
    count_series,
    load_counts,
    load_locations,
    period_start,
    refresh_views,
    representative,
)
from app.models.event import Location

HISTORY_WEEKS = 52
"""歷史期間最多幾個完整週。"""
DIGITS = 1
"""基準件數取到小數第幾位。"""


@dataclass(frozen=True)
class Baseline:
    """自動計算的基準與它的依據。"""

    baseline: float | None
    """時間窗內的基準件數；沒有歷史期間時為 None。"""
    observed: int
    """歷史期間內符合條件的件數。"""
    weeks: int
    """歷史期間的週數。"""
    history_start: date | None
    history_end: date | None
    window_days: int
    event_type: str | None
    location: str | None
    """比對用的地點名稱（別名已換成代表地點）。"""

    def basis(self) -> dict[str, Any]:
        """存進模擬輸入的依據（日期轉成 ISO 字串）。"""
        data = asdict(self)
        for key in ("history_start", "history_end"):
            value = data[key]
            data[key] = None if value is None else value.isoformat()
        del data["baseline"]
        return data


def history_range(
    as_of: date, earliest: date | None, weeks: int = HISTORY_WEEKS
) -> tuple[date, date] | None:
    """歷史期間（週一到週日，含頭尾）；`earliest` 是最早一筆件數的日期，沒有資料時傳 None。"""
    if weeks < 1:
        raise ValueError("weeks 至少為 1")
    this_week = period_start(as_of, "week")
    if earliest is None or period_start(earliest, "week") >= this_week:
        return None
    start = max(this_week - timedelta(weeks=weeks), period_start(earliest, "week"))
    return start, this_week - timedelta(days=1)


def compute_baseline(
    rows: Sequence[CountRow],
    *,
    as_of: date,
    window_days: int,
    event_type: str | None = None,
    location: str | None = None,
    weeks: int = HISTORY_WEEKS,
) -> Baseline:
    """由週件數算出基準；`location` 須已是代表地點名稱。最早日期依全部件數判斷（不分類別地點）。"""
    if window_days < 1:
        raise ValueError("window_days 至少為 1")
    earliest = min((r.period_start for r in rows), default=None)
    span = history_range(as_of, earliest, weeks)
    if span is None:
        return Baseline(None, 0, 0, None, None, window_days, event_type, location)
    start, end = span
    series = count_series(rows, "week", start, end, event_type=event_type, location=location)
    observed = sum(p.count for p in series)
    value = round(observed / len(series) * window_days / 7, DIGITS)
    return Baseline(value, observed, len(series), start, end, window_days, event_type, location)


async def representative_name(session: AsyncSession, name: str) -> str:
    """地點名稱對應的代表地點名稱（與 views 相同經 `mask_pii`）；找不到該地點時原樣回傳。"""
    location_id = await session.scalar(select(Location.id).where(Location.name == name))
    if location_id is None:
        return name
    rep = representative(location_id, await load_locations(session))
    return name if rep is None else mask_pii(rep.name)


async def load_baseline(
    session: AsyncSession,
    *,
    as_of: date,
    window_days: int,
    event_type: str | None = None,
    location: str | None = None,
) -> Baseline:
    """更新週件數 view 後計算基準（在目前交易內執行）。"""
    await refresh_views(session)
    rows = await load_counts(session, "week", end=as_of)
    name = None if location is None else await representative_name(session, location)
    return compute_baseline(
        rows, as_of=as_of, window_days=window_days, event_type=event_type, location=name
    )
