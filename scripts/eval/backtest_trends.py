"""趨勢回測（P6-T04；G6-2）：滾動切點，每個切點只用切點前已知的資料。

在 backend/ 執行：
    uv run python ../scripts/eval/backtest_trends.py --start 2026-01-01 --step week
    uv run python ../scripts/eval/backtest_trends.py --start 2026-01-01 --step month \
        --end 2026-09-30
邏輯在 backend/app/analytics/backtest.py。報告寫到 eval/reports/trends/，
只有件數與統計，不含地點與內文。
"""

import argparse
import asyncio
import sys
from datetime import UTC, date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    """把 backend/ 加入匯入路徑後執行。"""
    sys.path.insert(0, str(ROOT / "backend"))
    from app.analytics.backtest import (
        TAIPEI,
        EventRecord,
        backtest,
        load_records,
        write_report,
    )
    from app.db.session import get_engine, get_sessionmaker

    today = datetime.now(UTC).astimezone(TAIPEI).date()
    parser = argparse.ArgumentParser(description="趨勢回測")
    parser.add_argument(
        "--start", type=date.fromisoformat, required=True, help="第一個切點所在日期"
    )
    parser.add_argument(
        "--end", type=date.fromisoformat, default=today, help="最後一個切點所在日期"
    )
    parser.add_argument("--step", choices=["week", "month"], default="week", help="切點間隔")
    parser.add_argument("--out", type=Path, default=ROOT / "eval/reports/trends", help="報告目錄")
    args = parser.parse_args()

    async def run() -> list[EventRecord]:
        try:
            async with get_sessionmaker()() as session:
                return await load_records(session)
        finally:
            await get_engine().dispose()

    records = asyncio.run(run())
    result = backtest(records, args.start, args.end, args.step, as_of=today)
    path = write_report(result, args.out)
    overall = result.scopes["all"].summary
    print(f"事件 {result.events} 件、切點 {overall.cutoffs} 個（{result.start}～{result.end}）")
    print(
        f"預測評分 {overall.forecast_scored} 期、MAE {overall.forecast_mae}；"
        f"線上突增 {overall.online_anomalies}、事後突增 {overall.hindsight_anomalies}、"
        f"一致 {overall.true_positives}"
    )
    print(f"報告：{path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
