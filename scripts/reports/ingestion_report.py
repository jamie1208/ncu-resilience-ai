"""擷取穩定運行報告（P1-T10；G1-2、G1-3）。

在 backend/ 執行：
    uv run python ../scripts/reports/ingestion_report.py --hours 12
    uv run python ../scripts/reports/ingestion_report.py --hours 12 --check-success 0.98
邏輯在 backend/app/ingestion/report.py。輸出只有來源代碼與筆數，不含內文。
"""

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path


def main() -> int:
    """把 backend/ 加入匯入路徑後執行；`--check-success` 未通過時回傳 1。"""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
    from app.db.session import get_engine, get_sessionmaker
    from app.ingestion.report import build_report, passes, render_markdown

    parser = argparse.ArgumentParser(description="擷取穩定運行報告")
    parser.add_argument("--hours", type=int, default=12, help="報告區間（小時）")
    parser.add_argument("--check-success", type=float, help="成功率門檻，例如 0.98（G1-2）")
    parser.add_argument("--out", type=Path, help="另存 Markdown 到這個路徑")
    args = parser.parse_args()

    async def run() -> tuple[str, bool]:
        try:
            async with get_sessionmaker()() as session:
                report = await build_report(session, hours=args.hours, now=datetime.now(UTC))
        finally:
            await get_engine().dispose()
        ok = args.check_success is None or passes(report, args.check_success)
        return render_markdown(report), ok

    text, ok = asyncio.run(run())
    print(text, end="")
    if args.out is not None:
        args.out.write_text(text, encoding="utf-8")
    if not ok:
        print(
            f"未通過：成功率需 ≥ {args.check_success:.0%}、所有來源連續、重複寫入為 0",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
