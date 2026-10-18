"""P1-T09：標記命令列（匯入、統計與 G1-5 檢查、匯出路徑限制）。"""

from __future__ import annotations

import csv
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.labeling import cli
from app.labeling.dataset import load_labels
from app.labeling.export import EXPORT_COLUMNS

NOW = datetime(2026, 10, 18, 4, 0, tzinfo=UTC)
SECRET_TEXT = "這段內文不應出現在輸出中"


def write_batch(path: Path, n: int, *, start: int = 1, confirm: str = "y") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(EXPORT_COLUMNS))
        writer.writeheader()
        for i in range(start, start + n):
            writer.writerow(
                {
                    "item_id": str(uuid.UUID(int=i)),
                    "source_code": "ncu-announcements",
                    "content_hash": f"{i:064x}",
                    "published_at": f"2026-10-{1 + i % 28:02d}T08:00:00+00:00",
                    "url": f"https://www.ncu.edu.tw/{i}",
                    "text": SECRET_TEXT,
                    "is_campus_relevant": "y",
                    "category": "none",
                    "claim_type": "official",
                    "verification": "",
                    "event_group": "",
                    "related_groups": "",
                    "high_risk": "n",
                    "mentions_person": "n",
                    "tags": "",
                    "confirm": confirm,
                }
            )
    return path


@pytest.fixture
def data(tmp_path: Path) -> Path:
    return tmp_path / "data"


def run_import(data: Path, batch: Path) -> int:
    return cli.import_main([str(batch), "--data-root", str(data)], now=NOW)


def test_import_writes_labels_without_text(data: Path, capsys: pytest.CaptureFixture[str]) -> None:
    batch = write_batch(data / "raw" / "labeling" / "b1.csv", 3)
    assert run_import(data, batch) == 0
    labels_file = data / "labeled" / "labels.jsonl"
    assert len(load_labels(labels_file)) == 3
    assert SECRET_TEXT not in labels_file.read_text(encoding="utf-8")
    out = capsys.readouterr().out
    assert "新增 3" in out
    assert SECRET_TEXT not in out


def test_import_twice_updates_instead_of_duplicating(
    data: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    batch = write_batch(data / "raw" / "labeling" / "b1.csv", 2)
    run_import(data, batch)
    assert run_import(data, batch) == 0
    assert "更新 2" in capsys.readouterr().out
    assert len(load_labels(data / "labeled" / "labels.jsonl")) == 2


def test_invalid_batch_changes_nothing(data: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run_import(data, write_batch(data / "raw" / "labeling" / "b1.csv", 2))
    before = (data / "labeled" / "labels.jsonl").read_bytes()
    bad = write_batch(data / "raw" / "labeling" / "b2.csv", 1, start=10)
    bad.write_text(
        bad.read_text(encoding="utf-8-sig").replace(",none,", ",other,"), encoding="utf-8"
    )
    assert run_import(data, bad) == 1
    assert (data / "labeled" / "labels.jsonl").read_bytes() == before
    err = capsys.readouterr().err
    assert "category" in err
    assert SECRET_TEXT not in err


def test_stats_reports_and_checks_minimum(data: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run_import(data, write_batch(data / "raw" / "labeling" / "b1.csv", 5))
    capsys.readouterr()
    assert cli.stats_main(["--data-root", str(data)]) == 0
    out = capsys.readouterr().out
    assert "總筆數：5" in out
    assert cli.stats_main(["--data-root", str(data), "--check-min", "5"]) == 0
    assert cli.stats_main(["--data-root", str(data), "--check-min", "6"]) == 1


def test_stats_writes_split_files(data: Path) -> None:
    run_import(data, write_batch(data / "raw" / "labeling" / "b1.csv", 10))
    assert cli.stats_main(["--data-root", str(data), "--write-split"]) == 0
    dev = (data / "labeled" / "dev.jsonl").read_text(encoding="utf-8").splitlines()
    test = (data / "labeled" / "test.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(dev) + len(test) == 10
    assert len(test) == 3


def test_stats_with_no_labels_fails_minimum(data: Path) -> None:
    assert cli.stats_main(["--data-root", str(data), "--check-min", "100"]) == 1


@pytest.mark.parametrize("out", ["labeled/batch.csv", "labeling/batch.csv", "../batch.csv"])
def test_export_output_must_stay_in_raw(data: Path, out: str) -> None:
    """含內文的匯出檔只能放在不進 git 的 data/raw/ 底下。"""
    with pytest.raises(SystemExit):
        cli.export_output_path(data, data / out)


def test_export_default_output_is_in_raw_labeling(data: Path) -> None:
    path = cli.export_output_path(data, None, now=NOW)
    assert path == data / "raw" / "labeling" / "batch-20261018-1200.csv"
