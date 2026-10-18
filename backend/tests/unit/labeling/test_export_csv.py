"""P1-T09：匯出給使用者標記的 CSV（不連資料庫的部分）。"""

from __future__ import annotations

import csv
import uuid
from datetime import UTC, datetime
from pathlib import Path

from app.labeling.dataset import parse_labeled_csv
from app.labeling.export import EXPORT_COLUMNS, ExportRow, write_export_csv

NOW = datetime(2026, 10, 18, 4, 0, tzinfo=UTC)


def export_row(n: int, source: str = "ncu-oga-news", text: str = "工程五館停電通知") -> ExportRow:
    return ExportRow(
        item_id=uuid.UUID(int=n),
        source_code=source,
        content_hash=f"{n:064x}",
        published_at=datetime(2026, 10, 17, 8, 0, tzinfo=UTC),
        url=f"https://www.oga.ncu.edu.tw/news/{n}",
        text=text,
    )


def read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def test_csv_has_bom_columns_and_prelabels(tmp_path: Path) -> None:
    path = tmp_path / "labeling" / "batch.csv"
    count = write_export_csv(path, [export_row(1), export_row(2, "facebook", "機車被拖吊\n第二行")])
    assert count == 2
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")  # Excel 需要 BOM 才能正確顯示中文
    rows = read(path)
    assert list(rows[0]) == list(EXPORT_COLUMNS)
    official, social = rows
    assert official["claim_type"] == "official"
    assert official["verification"] == "verified"
    assert official["is_campus_relevant"] == "y"
    assert official["category"] == "facility"
    assert official["confirm"] == ""  # 一定要人工確認
    assert social["text"] == "機車被拖吊\n第二行"
    assert social["verification"] == "unverified"
    assert social["is_campus_relevant"] == ""
    assert social["category"] == "traffic"


def test_exported_file_is_not_importable_until_confirmed(tmp_path: Path) -> None:
    path = tmp_path / "batch.csv"
    write_export_csv(path, [export_row(1)])
    assert parse_labeled_csv(path, now=NOW) == []


def test_confirmed_export_can_be_imported(tmp_path: Path) -> None:
    path = tmp_path / "batch.csv"
    write_export_csv(path, [export_row(1)])
    rows = read(path)
    rows[0].update(event_group="evt-1", confirm="y")
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(EXPORT_COLUMNS))
        writer.writeheader()
        writer.writerows(rows)
    [label] = parse_labeled_csv(path, now=NOW)
    assert label.content_hash == f"{1:064x}"
    assert label.published_at == datetime(2026, 10, 17, 8, 0, tzinfo=UTC)
