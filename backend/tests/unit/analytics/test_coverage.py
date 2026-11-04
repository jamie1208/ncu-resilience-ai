"""P7-T06：S09 的資料覆蓋率（實際擷取天數 ÷ 應擷取天數）。

- 每個核准來源在統計期間內「有成功擷取的台北日」÷「應擷取的天數」，所有來源合計為 ratio；
- 統計期間中才新增的來源，從新增當天起算，並在 note 說明「新增來源」；
- 擷取缺漏在 note 列出來源與日期；
- 分級：全部來源都有擷取 → full；有缺漏或來源組成有變 → partial；大部分期間缺資料 → low；
- 全部缺資料（或沒有來源）為 low。
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.analytics.coverage import (
    FULL_RATIO,
    LOW_RATIO,
    MAX_NOTE_LENGTH,
    SourceActivity,
    compute_coverage,
)

START = date(2026, 10, 1)
END = date(2026, 10, 31)  # 31 天


def days(first: date, last: date) -> frozenset[date]:
    return frozenset(first + timedelta(n) for n in range((last - first).days + 1))


def every_day(code: str, *, added_on: date = date(2026, 9, 1)) -> SourceActivity:
    return SourceActivity(
        code=code, added_on=added_on, enabled=True, ingested_days=days(START, END)
    )


def test_all_sources_every_day_is_full() -> None:
    cov = compute_coverage([every_day("ncu-news"), every_day("cwa-alerts")], START, END)
    assert cov.level == "full"
    assert cov.ratio == 1.0
    assert cov.note is None
    assert cov.to_input() == {"level": "full", "ratio": 1.0, "note": None}


def test_ingestion_gap_is_partial_and_noted() -> None:
    gap = days(START, END) - days(date(2026, 10, 12), date(2026, 10, 14)) - {date(2026, 10, 20)}
    sources = [
        every_day("ncu-news"),
        SourceActivity(code="ncu-cc", added_on=date(2026, 9, 1), enabled=True, ingested_days=gap),
    ]
    cov = compute_coverage(sources, START, END)
    assert cov.level == "partial"
    assert cov.ratio == pytest.approx((31 + 27) / 62)
    assert cov.note is not None
    assert "ncu-cc 缺 4 天" in cov.note
    assert "10/12–10/14" in cov.note and "10/20" in cov.note
    assert "ncu-news" not in cov.note


def test_small_gap_on_one_source_still_not_full() -> None:
    # 合計比例仍高，但「所有核准來源都有擷取」不成立
    many = [every_day(f"src-{i}") for i in range(30)]
    short = SourceActivity(
        code="ncu-lib",
        added_on=date(2026, 9, 1),
        enabled=True,
        ingested_days=days(START, date(2026, 10, 20)),
    )
    cov = compute_coverage([*many, short], START, END)
    assert cov.ratio is not None and cov.ratio >= FULL_RATIO
    assert cov.level == "partial"
    assert "ncu-lib 缺 11 天" in (cov.note or "")


def test_new_source_counts_from_added_day_and_is_noted() -> None:
    added = date(2026, 10, 22)
    sources = [
        every_day("ncu-news"),
        SourceActivity(
            code="cwa-alerts", added_on=added, enabled=True, ingested_days=days(added, END)
        ),
    ]
    cov = compute_coverage(sources, START, END)
    # 新增前的天數不算缺漏：(31 + 10) / (31 + 10)
    assert cov.ratio == 1.0
    assert cov.level == "partial"  # 來源組成有變
    assert "cwa-alerts 於 10/22 起新增" in (cov.note or "")
    assert "缺" not in (cov.note or "")


def test_source_added_after_period_is_ignored() -> None:
    later = SourceActivity(
        code="ncu-oia", added_on=END + timedelta(1), enabled=True, ingested_days=frozenset()
    )
    cov = compute_coverage([every_day("ncu-news"), later], START, END)
    assert cov.level == "full"
    assert cov.ratio == 1.0


def test_disabled_source_without_ingestion_is_excluded() -> None:
    off = SourceActivity(
        code="ncu-sec", added_on=date(2026, 9, 1), enabled=False, ingested_days=frozenset()
    )
    cov = compute_coverage([every_day("ncu-news"), off], START, END)
    assert cov.level == "full"


def test_disabled_source_with_ingestion_counts_gaps() -> None:
    off = SourceActivity(
        code="ncu-sec",
        added_on=date(2026, 9, 1),
        enabled=False,
        ingested_days=days(START, date(2026, 10, 10)),
    )
    cov = compute_coverage([every_day("ncu-news"), off], START, END)
    assert cov.level == "partial"
    assert "ncu-sec 缺 21 天" in (cov.note or "")


def test_mostly_missing_is_low() -> None:
    sparse = [
        SourceActivity(
            code=code,
            added_on=date(2026, 9, 1),
            enabled=True,
            ingested_days=days(START, date(2026, 10, 10)),
        )
        for code in ("ncu-news", "ncu-cc")
    ]
    cov = compute_coverage(sparse, START, END)
    assert cov.ratio is not None and cov.ratio < LOW_RATIO
    assert cov.level == "low"


def test_all_missing_is_low() -> None:
    empty = [
        SourceActivity(
            code=code, added_on=date(2026, 9, 1), enabled=True, ingested_days=frozenset()
        )
        for code in ("ncu-news", "ncu-cc")
    ]
    cov = compute_coverage(empty, START, END)
    assert cov.level == "low"
    assert cov.ratio == 0.0
    assert "沒有任何成功的擷取" in (cov.note or "")


def test_no_sources_is_low_without_ratio() -> None:
    cov = compute_coverage([], START, END)
    assert cov.level == "low"
    assert cov.ratio is None
    assert cov.note


def test_ingestion_outside_period_is_ignored() -> None:
    outside = SourceActivity(
        code="ncu-news",
        added_on=date(2026, 9, 1),
        enabled=True,
        ingested_days=days(START, END) | {date(2026, 9, 15), date(2026, 11, 2)},
    )
    cov = compute_coverage([outside], START, END)
    assert cov.ratio == 1.0
    assert cov.sources[0].ingested_days == 31


def test_note_is_bounded() -> None:
    patchy = [
        SourceActivity(
            code=f"source-with-long-code-{i}",
            added_on=date(2026, 9, 1),
            enabled=True,
            ingested_days=frozenset(START + timedelta(n) for n in range(0, 31, 2)),
        )
        for i in range(20)
    ]
    cov = compute_coverage(patchy, START, END)
    assert cov.note is not None
    assert len(cov.note) <= MAX_NOTE_LENGTH
    assert "等" in cov.note


def test_end_before_start_rejected() -> None:
    with pytest.raises(ValueError):
        compute_coverage([], END, START)
