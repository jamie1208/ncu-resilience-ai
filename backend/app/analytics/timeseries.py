"""時序統計與異常偵測（P6-T03）：週／月件數、地點分布、EWMA、Poisson 變化偵測。

完全確定性，不呼叫模型。資料來源是 migration 0016 的 materialized views
（`analytics_event_counts_week`／`_month`，以 `refresh_views` 更新、`load_counts` 讀取），
`bucket()` 是同一套分組規則的 Python 版本，供回測（P6-T04）以切點前的事件重算。

事件日期與再發生偵測（`app/resilience/recurrence.py`）一致：S02 的發生日期（台北時間），
沒有時用建立時間的台北日期；查證為不實（false）與類別為 none 的事件不列入。

變化偵測只看「突增」：第 t 期的期望值是第 t 期之前各期件數的 EWMA（不看當期與未來），
在 Poisson(期望值) 下觀測值不小於當期件數的機率低於門檻、且件數達到下限才判為異常。
件數是「被回報、被彙整成事件」的數量，不等於實際發生次數（聲量≠事件數）。
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import pairwise
from typing import Literal

from sqlalchemy import column, select, table, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.pii import mask_pii

Frequency = Literal["week", "month"]
UNKNOWN_LOCATION = "地點不明"
# 與 infra/migrations/versions/0016_analytics_views.py 同步
VIEWS: dict[Frequency, str] = {
    "week": "analytics_event_counts_week",
    "month": "analytics_event_counts_month",
}


@dataclass(frozen=True)
class EventPoint:
    """一個列入統計的事件。"""

    occurred_on: date
    """台北時間的發生日期。"""
    event_type: str
    location_name: str | None


@dataclass(frozen=True)
class CountRow:
    """某週期、類別、地點的事件件數（materialized view 的一列）。"""

    period_start: date
    event_type: str
    location_name: str | None
    count: int


@dataclass(frozen=True)
class PeriodCount:
    """補零後序列中的一個週期。"""

    period_start: date
    count: int


@dataclass(frozen=True)
class LocationShare:
    """地點分布中的一個地點；地點不明合併為 `UNKNOWN_LOCATION`。"""

    location: str
    count: int
    share: float


@dataclass(frozen=True)
class Anomaly:
    """被判為突增的週期。"""

    period_start: date
    observed: int
    expected: float
    """只用之前各期算出的 EWMA（有下限 `min_expected`）。"""
    p_value: float
    """Poisson(expected) 下 P(X ≥ observed)。"""


@dataclass(frozen=True)
class DetectionParams:
    """Poisson 變化偵測的參數。"""

    alpha: float = 0.3
    """EWMA 平滑係數，(0, 1]。"""
    min_history: int = 4
    """至少要有幾個之前的週期才判定。"""
    p_threshold: float = 0.001
    """p 值低於此值才算異常，(0, 1)。"""
    min_count: int = 5
    """當期件數至少多少才算異常，避免零星事件被放大。"""
    min_expected: float = 0.5
    """期望值下限，避免長期 0 件時任何件數都變成極小 p 值。"""

    def __post_init__(self) -> None:
        if not 0 < self.alpha <= 1:
            raise ValueError("alpha 必須在 (0, 1]")
        if self.min_history < 1:
            raise ValueError("min_history 至少為 1")
        if not 0 < self.p_threshold < 1:
            raise ValueError("p_threshold 必須在 (0, 1)")
        if self.min_count < 1:
            raise ValueError("min_count 至少為 1")
        if self.min_expected <= 0:
            raise ValueError("min_expected 必須大於 0")


def _row_key(r: CountRow) -> tuple[date, str, str]:
    """排序鍵：週期、類別、地點（地點不明排在最前）。"""
    return (r.period_start, r.event_type, r.location_name or "")


# ---------- 分桶 ----------


def period_start(day: date, freq: Frequency) -> date:
    """`day` 所在週期的起日：週以 ISO 週一起算，月為該月 1 日。"""
    if freq == "week":
        return day - timedelta(days=day.weekday())
    if freq == "month":
        return day.replace(day=1)
    raise ValueError(f"不支援的頻率：{freq}")


def _next_period(start: date, freq: Frequency) -> date:
    if freq == "week":
        return start + timedelta(weeks=1)
    return date(start.year + start.month // 12, start.month % 12 + 1, 1)


def periods(start: date, end: date, freq: Frequency) -> list[date]:
    """`start` 到 `end`（含）涵蓋的所有週期起日。"""
    if end < start:
        raise ValueError("end 不得早於 start")
    out = []
    current, last = period_start(start, freq), period_start(end, freq)
    while current <= last:
        out.append(current)
        current = _next_period(current, freq)
    return out


def bucket(points: Iterable[EventPoint], freq: Frequency) -> list[CountRow]:
    """依 (週期, 類別, 地點) 計數；與 materialized views 的分組相同。"""
    counts: defaultdict[tuple[date, str, str | None], int] = defaultdict(int)
    for p in points:
        counts[(period_start(p.occurred_on, freq), p.event_type, p.location_name)] += 1
    return sorted((CountRow(*key, n) for key, n in counts.items()), key=_row_key)


# ---------- 序列與分布 ----------


def _selected(
    rows: Iterable[CountRow],
    start: date,
    end: date,
    freq: Frequency,
    event_type: str | None,
    location: str | None,
) -> Iterable[CountRow]:
    first, last = period_start(start, freq), period_start(end, freq)
    for r in rows:
        if not first <= r.period_start <= last:
            continue
        if event_type is not None and r.event_type != event_type:
            continue
        if location is not None and r.location_name != location:
            continue
        yield r


def count_series(
    rows: Iterable[CountRow],
    freq: Frequency,
    start: date,
    end: date,
    *,
    event_type: str | None = None,
    location: str | None = None,
) -> list[PeriodCount]:
    """`start`～`end` 每個週期的件數（沒有事件的週期為 0），可依類別、地點篩選。"""
    totals: defaultdict[date, int] = defaultdict(int)
    for r in _selected(rows, start, end, freq, event_type, location):
        totals[r.period_start] += r.count
    return [PeriodCount(p, totals[p]) for p in periods(start, end, freq)]


def location_distribution(
    rows: Iterable[CountRow],
    start: date,
    end: date,
    *,
    freq: Frequency = "week",
    event_type: str | None = None,
) -> list[LocationShare]:
    """`start`～`end` 各地點的件數與占比，依件數由多到少（同數依名稱，地點不明排在最後）。"""
    totals: defaultdict[str, int] = defaultdict(int)
    for r in _selected(rows, start, end, freq, event_type, None):
        totals[r.location_name or UNKNOWN_LOCATION] += r.count
    grand = sum(totals.values())
    ordered = sorted(totals.items(), key=lambda kv: (-kv[1], kv[0] == UNKNOWN_LOCATION, kv[0]))
    return [LocationShare(name, n, n / grand) for name, n in ordered]


# ---------- EWMA 與 Poisson ----------


def ewma(values: Sequence[float], alpha: float) -> list[float]:
    """指數加權移動平均：s₀ = x₀，sₜ = α·xₜ + (1 − α)·sₜ₋₁。"""
    if not 0 < alpha <= 1:
        raise ValueError("alpha 必須在 (0, 1]")
    out: list[float] = []
    for x in values:
        out.append(x if not out else alpha * x + (1 - alpha) * out[-1])
    return out


def poisson_sf(k: int, lam: float) -> float:
    """Poisson(lam) 下 P(X ≥ k)。"""
    if lam <= 0:
        raise ValueError("lam 必須大於 0")
    if k <= 0:
        return 1.0
    if k <= lam:
        cdf = sum(math.exp(i * math.log(lam) - lam - math.lgamma(i + 1)) for i in range(k))
        return max(0.0, 1.0 - cdf)
    # 右尾直接加總，避免 1 − CDF 的抵銷誤差；k > lam 時各項遞減
    term = math.exp(k * math.log(lam) - lam - math.lgamma(k + 1))
    total, i = 0.0, k
    while term > total * 1e-17:
        total += term
        i += 1
        term *= lam / i
    return min(1.0, total)


def detect_changes(
    series: Sequence[tuple[date, int]] | Sequence[PeriodCount],
    params: DetectionParams | None = None,
) -> list[Anomaly]:
    """找出突增的週期；`series` 必須依週期遞增且不重複（建議用 `count_series` 的結果）。"""
    params = params or DetectionParams()
    pairs = [(s.period_start, s.count) if isinstance(s, PeriodCount) else s for s in series]
    for (a, _), (b, _) in pairwise(pairs):
        if b <= a:
            raise ValueError("series 必須依週期遞增且不重複")
    anomalies: list[Anomaly] = []
    smoothed: float | None = None
    for i, (start, observed) in enumerate(pairs):
        if i >= params.min_history and smoothed is not None and observed >= params.min_count:
            expected = max(smoothed, params.min_expected)
            p = poisson_sf(observed, expected)
            if p < params.p_threshold:
                anomalies.append(Anomaly(start, observed, expected, p))
        # 判定完當期才把當期納入 EWMA，期望值永遠只來自之前的週期
        smoothed = (
            float(observed)
            if smoothed is None
            else params.alpha * observed + (1 - params.alpha) * smoothed
        )
    return anomalies


# ---------- 資料庫 ----------


async def refresh_views(session: AsyncSession) -> None:
    """重新整理週／月 materialized views（在目前交易內執行）。"""
    for name in VIEWS.values():
        await session.execute(text(f"REFRESH MATERIALIZED VIEW {name}"))


async def load_counts(
    session: AsyncSession,
    freq: Frequency,
    *,
    start: date | None = None,
    end: date | None = None,
) -> list[CountRow]:
    """從 materialized view 讀出件數，地點名稱經 `mask_pii()`；可依日期範圍篩選週期。"""
    if freq not in VIEWS:
        raise ValueError(f"不支援的頻率：{freq}")
    view = table(
        VIEWS[freq],
        column("period_start"),
        column("event_type"),
        column("location_name"),
        column("event_count"),
    )
    stmt = select(view.c.period_start, view.c.event_type, view.c.location_name, view.c.event_count)
    if start is not None:
        stmt = stmt.where(view.c.period_start >= period_start(start, freq))
    if end is not None:
        stmt = stmt.where(view.c.period_start <= period_start(end, freq))
    result = await session.execute(stmt)
    return sorted(
        (
            CountRow(row[0], row[1], mask_pii(row[2]) if row[2] is not None else None, row[3])
            for row in result.all()
        ),
        key=_row_key,
    )
