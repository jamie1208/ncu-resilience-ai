"""標記資料集的命令列（P1-T09，ADR-0009）；`scripts/labeling/*.py` 是這裡的入口。

    cd backend
    uv run python ../scripts/labeling/export_for_labeling.py --limit 100
    uv run python ../scripts/labeling/import_labels.py ../data/raw/labeling/batch-….csv
    uv run python ../scripts/labeling/dataset_stats.py --write-split --check-min 100

輸出只包含筆數與路徑，不輸出內文。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from app.ingestion.normalize import TAIPEI
from app.labeling.dataset import (
    LabelImportError,
    load_labels,
    merge_labels,
    parse_labeled_csv,
    save_labels,
    split_dataset,
    stats,
    write_split,
)
from app.labeling.export import fetch_for_labeling, write_export_csv

DATA_ROOT = Path(__file__).resolve().parents[3] / "data"
"""repo 根目錄的 data/。"""


def _labels_file(data_root: Path) -> Path:
    return data_root / "labeled" / "labels.jsonl"


def _parser(description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT, help="data/ 資料夾位置")
    return parser


# --- 匯出 -------------------------------------------------------------------------------


def export_output_path(data_root: Path, out: Path | None, *, now: datetime | None = None) -> Path:
    """決定匯出檔位置；含內文的檔案只能放在 `data/raw/` 底下（不進 git）。"""
    raw = (data_root / "raw").resolve()
    if out is None:
        stamp = (now or datetime.now(UTC)).astimezone(TAIPEI).strftime("%Y%m%d-%H%M")
        return data_root / "raw" / "labeling" / f"batch-{stamp}.csv"
    if not out.resolve().is_relative_to(raw):
        raise SystemExit(f"匯出檔含內文，只能放在 {raw} 底下")
    return out


def export_main(argv: Sequence[str] | None = None) -> int:
    """匯出尚未標記的文章為 CSV（含規則式預標）。"""
    parser = _parser("匯出待標記文章（CSV，含規則式預標）")
    parser.add_argument("--limit", type=int, default=100, help="最多匯出幾篇（1–1000）")
    parser.add_argument("--source", action="append", help="只匯出指定來源，可重複")
    parser.add_argument("--out", type=Path, help="輸出 CSV 路徑（必須在 data/raw/ 底下）")
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 1000:
        parser.error("--limit 必須介於 1 與 1000 之間")
    out = export_output_path(args.data_root, args.out)
    exclude = set(load_labels(_labels_file(args.data_root)))

    async def run() -> int:
        from app.db.session import get_engine, get_sessionmaker

        try:
            async with get_sessionmaker()() as session:
                rows = await fetch_for_labeling(
                    session, exclude=exclude, limit=args.limit, sources=args.source
                )
        finally:
            await get_engine().dispose()
        return write_export_csv(out, rows)

    count = asyncio.run(run())
    print(f"已匯出 {count} 篇到 {out}（已排除 {len(exclude)} 篇已標記）")
    print("在試算表填寫標記欄位，確認的列在 confirm 欄填 y，存檔後執行 import_labels.py。")
    return 0


# --- 匯入 -------------------------------------------------------------------------------


def import_main(argv: Sequence[str] | None = None, *, now: datetime | None = None) -> int:
    """匯入使用者填好的 CSV；不合格時整個檔案不匯入，回傳 1。"""
    parser = _parser("匯入人工確認的標記")
    parser.add_argument("csv", type=Path, help="填好的 CSV")
    args = parser.parse_args(argv)
    labels_file = _labels_file(args.data_root)
    try:
        new = parse_labeled_csv(args.csv, now=now or datetime.now(UTC))
        existing = load_labels(labels_file)
    except (LabelImportError, FileNotFoundError) as exc:
        print(f"匯入失敗，沒有寫入任何標記：{exc}", file=sys.stderr)
        return 1
    merged, added, updated = merge_labels(existing, new)
    save_labels(labels_file, merged)
    print(f"新增 {added} 筆、更新 {updated} 筆；標記檔共 {len(merged)} 筆（{labels_file}）")
    return 0


# --- 統計 -------------------------------------------------------------------------------


def stats_main(argv: Sequence[str] | None = None) -> int:
    """統計標記資料集；`--check-min N` 未達 N 筆時回傳 1（G1-5）。"""
    parser = _parser("標記資料集統計與 dev／test 切分")
    parser.add_argument("--test-ratio", type=float, default=0.3, help="test 占事件組的比例")
    parser.add_argument("--write-split", action="store_true", help="寫出 dev.jsonl、test.jsonl")
    parser.add_argument("--check-min", type=int, help="至少要有幾筆人工確認的標記")
    args = parser.parse_args(argv)
    labels_file = _labels_file(args.data_root)
    try:
        labels = list(load_labels(labels_file).values())
    except LabelImportError as exc:
        print(f"標記檔不合格：{exc}", file=sys.stderr)
        return 1
    split = split_dataset(labels, test_ratio=args.test_ratio) if labels else {}
    result = stats(labels, split)
    print(f"總筆數：{result.total}（dev {result.dev}、test {result.test}）")
    print(f"事件組：{result.event_groups}；高風險：{result.high_risk}")
    print("類別：" + ("、".join(f"{k} {v}" for k, v in result.by_category.items()) or "—"))
    print("來源：" + ("、".join(f"{k} {v}" for k, v in result.by_source.items()) or "—"))
    if args.write_split and labels:
        write_split(labels_file.parent, labels, split)
        print(f"已寫出 {labels_file.parent / 'dev.jsonl'}、{labels_file.parent / 'test.jsonl'}")
    if args.check_min is not None and result.total < args.check_min:
        print(f"未達 {args.check_min} 筆（目前 {result.total} 筆）", file=sys.stderr)
        return 1
    return 0
