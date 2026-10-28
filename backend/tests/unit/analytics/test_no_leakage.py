"""P6-T04：趨勢回測不使用未來資料（G6-2）。

切點 c 代表「台北時間 c 當天 00:00 當下」：只有在那之前已建立（created_at）且已發生的事件可用。
1. `snapshot` 排除切點後才建立的事件（即使發生在切點前，也就是晚到的回報）與切點後才發生的事件，
   台北時間的日界正確；
2. 在任何切點加入切點後才知道的事件，該切點的預測、線上偵測與已知件數都不變；
3. 未來突增不會讓切點當下提前偵測到，也不會改變預測；
4. 實際值（事後全部資料）只用來評分，且只對已完整結束的週期評分；
5. 報告不含地點與內文，寫入指定目錄。
"""

from __future__ import annotations

import json
import random
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.analytics.backtest import (
    EventRecord,
    backtest,
    cutoffs,
    evaluate_cutoff,
    snapshot,
    write_report,
)
from app.analytics.timeseries import DetectionParams

TAIPEI = ZoneInfo("Asia/Taipei")
START = date(2026, 1, 5)  # 週一


def known(day: date, hour: int = 12) -> datetime:
    """台北時間 `day` 的 `hour` 點，轉成 aware UTC。"""
    return datetime.combine(day, time(hour), TAIPEI).astimezone(UTC)


def rec(occurred: date, created: datetime | None = None, event_type: str = "theft") -> EventRecord:
    return EventRecord(
        occurred_on=occurred, known_at=created or known(occurred), event_type=event_type
    )


def steady(weeks: int, per_week: int = 2, *, seed: int = 7) -> list[EventRecord]:
    """每週固定件數，回報在發生後 0–2 天內建立。"""
    rng = random.Random(seed)  # noqa: S311 — 固定種子的合成資料
    out = []
    for w in range(weeks):
        for _ in range(per_week):
            day = START + timedelta(weeks=w, days=rng.randrange(7))
            out.append(rec(day, known(day + timedelta(days=rng.randrange(3)), rng.randrange(24))))
    return out


# ---------- snapshot ----------


def test_snapshot_excludes_late_reports_and_future_events() -> None:
    cutoff = date(2026, 3, 9)
    records = [
        rec(date(2026, 3, 2)),  # 切點前發生、切點前建立 → 可用
        rec(date(2026, 3, 2), known(date(2026, 3, 10))),  # 晚到的回報 → 不可用
        rec(date(2026, 3, 9), known(date(2026, 3, 8))),  # 發生日在切點當天 → 不可用
        rec(date(2026, 3, 12)),  # 未來事件 → 不可用
    ]
    points = snapshot(records, cutoff)
    assert [p.occurred_on for p in points] == [date(2026, 3, 2)]


def test_snapshot_uses_taipei_midnight_boundary() -> None:
    cutoff = date(2026, 3, 9)
    day = date(2026, 3, 5)
    # 台北 3/8 23:30 = UTC 3/8 15:30，在切點前；台北 3/9 00:10 = UTC 3/8 16:10，不在切點前
    before = rec(day, datetime(2026, 3, 8, 15, 30, tzinfo=UTC))
    after = rec(day, datetime(2026, 3, 8, 16, 10, tzinfo=UTC))
    assert len(snapshot([before, after], cutoff)) == 1
    assert len(snapshot([after], cutoff)) == 0


def test_event_record_requires_aware_time() -> None:
    with pytest.raises(ValueError, match="aware"):
        EventRecord(occurred_on=START, known_at=datetime(2026, 1, 5, 12), event_type="theft")


# ---------- 切點 ----------


def test_cutoffs_align_to_period_starts() -> None:
    assert cutoffs(date(2026, 1, 7), date(2026, 1, 26), "week") == [
        date(2026, 1, 5),
        date(2026, 1, 12),
        date(2026, 1, 19),
        date(2026, 1, 26),
    ]
    assert cutoffs(date(2026, 1, 15), date(2026, 3, 2), "month") == [
        date(2026, 1, 1),
        date(2026, 2, 1),
        date(2026, 3, 1),
    ]


# ---------- 不洩漏 ----------


@pytest.mark.parametrize("seed", range(5))
def test_online_results_ignore_anything_known_after_cutoff(seed: int) -> None:
    base = steady(20, seed=seed)
    rng = random.Random(seed + 100)  # noqa: S311 — 固定種子的合成資料
    for cutoff in cutoffs(START + timedelta(weeks=6), START + timedelta(weeks=19), "week"):
        cutoff_at = datetime.combine(cutoff, time.min, TAIPEI)
        future = []
        for _ in range(40):
            occurred = cutoff + timedelta(days=rng.randrange(-60, 60))
            created = max(cutoff_at, known(occurred)) + timedelta(minutes=rng.randrange(0, 10_000))
            future.append(rec(occurred, created, rng.choice(["theft", "facility"])))
        clean = evaluate_cutoff(snapshot(base, cutoff), cutoff, "week")
        leaked = evaluate_cutoff(snapshot(base + future, cutoff), cutoff, "week")
        assert clean == leaked


def test_future_spike_is_not_seen_at_earlier_cutoffs() -> None:
    spike_week = START + timedelta(weeks=12)
    spike = [rec(spike_week + timedelta(days=i % 5)) for i in range(25)]
    records = steady(16) + spike
    result = backtest(
        records,
        START + timedelta(weeks=6),
        START + timedelta(weeks=15),
        "week",
        as_of=START + timedelta(weeks=16),
    )
    rows = {r.cutoff: r for r in result.scopes["all"].rows}
    without = backtest(
        steady(16),
        START + timedelta(weeks=6),
        START + timedelta(weeks=15),
        "week",
        as_of=START + timedelta(weeks=16),
    )
    rows_without = {r.cutoff: r for r in without.scopes["all"].rows}

    for cutoff, row in rows.items():
        if cutoff <= spike_week:
            # 突增那週開始時與之前：預測、線上偵測、已知件數都和沒有突增時一樣
            other = rows_without[cutoff]
            assert row.forecast == other.forecast
            assert row.online_anomaly == other.online_anomaly
            assert row.known_events == other.known_events
            assert not row.online_anomaly
    # 突增那週結束後的下一個切點才偵測到；事後全部資料也判為異常
    after = rows[spike_week + timedelta(weeks=1)]
    assert after.evaluated_period == spike_week
    assert after.online_anomaly and after.hindsight_anomaly
    # 實際值用事後全部資料評分：突增那週的實際件數包含突增
    assert rows[spike_week].actual is not None and rows[spike_week].actual >= 25


def test_late_reports_count_only_in_hindsight() -> None:
    week = START + timedelta(weeks=5)
    cutoff = week + timedelta(weeks=1)
    late = [rec(week + timedelta(days=1), known(cutoff + timedelta(days=2))) for _ in range(3)]
    on_time = [rec(START + timedelta(weeks=w)) for w in range(8)]  # 發生當天就建立
    records = on_time + late
    result = backtest(records, cutoff, cutoff, "week", as_of=START + timedelta(weeks=8))
    row = result.scopes["all"].rows[0]
    assert row.evaluated_period == week
    assert row.final_count == row.known_count + 3
    assert row.completeness is not None and row.completeness < 1


def test_actual_only_for_completed_periods() -> None:
    records = steady(10)
    as_of = START + timedelta(weeks=9, days=3)  # 第 9 週還沒結束
    result = backtest(
        records, START + timedelta(weeks=6), START + timedelta(weeks=9), "week", as_of=as_of
    )
    rows = {r.cutoff: r for r in result.scopes["all"].rows}
    assert rows[START + timedelta(weeks=8)].actual is not None
    assert rows[START + timedelta(weeks=9)].actual is None
    assert result.scopes["all"].summary.forecast_scored == 3


def test_cutoffs_after_as_of_are_rejected() -> None:
    with pytest.raises(ValueError, match="as_of"):
        backtest(
            steady(4), START, START + timedelta(weeks=5), "week", as_of=START + timedelta(weeks=4)
        )


def test_insufficient_history_gives_no_forecast() -> None:
    result = backtest([], START, START, "week", as_of=START + timedelta(weeks=1))
    row = result.scopes["all"].rows[0]
    assert row.forecast is None and row.known_events == 0
    assert result.scopes["all"].summary.forecast_mae is None
    assert result.scopes["all"].summary.precision is None


def test_scopes_split_by_event_type() -> None:
    records = steady(10) + [
        rec(START + timedelta(weeks=w), event_type="facility") for w in range(10)
    ]
    result = backtest(
        records,
        START + timedelta(weeks=4),
        START + timedelta(weeks=8),
        "week",
        as_of=START + timedelta(weeks=10),
    )
    assert set(result.scopes) == {"all", "theft", "facility"}
    facility = {r.cutoff: r for r in result.scopes["facility"].rows}
    assert facility[START + timedelta(weeks=5)].final_count == 1


# ---------- 報告 ----------


def test_report_written_without_locations(tmp_path: Path) -> None:
    result = backtest(
        steady(12),
        START + timedelta(weeks=4),
        START + timedelta(weeks=10),
        "week",
        as_of=START + timedelta(weeks=12),
        params=DetectionParams(min_count=3),
    )
    path = write_report(result, tmp_path, generated_at=datetime(2026, 4, 1, tzinfo=UTC))
    assert path.parent == tmp_path and path.suffix == ".json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["step"] == "week" and data["start"] == "2026-02-02"
    assert data["params"]["min_count"] == 3
    assert "聲量" in data["notes"][0]
    assert set(data["scopes"]) == {"all", "theft"}
    row = data["scopes"]["all"]["rows"][0]
    assert {"cutoff", "evaluated_period", "known_events", "forecast", "actual"} <= set(row)
    assert "location" not in path.read_text(encoding="utf-8")
