"""P1-T09：CSV 標記匯入、標記檔讀寫、dev／test 切分與統計（ADR-0009）。"""

from __future__ import annotations

import csv
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

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
from app.labeling.export import EXPORT_COLUMNS
from app.labeling.models import Category, LabelRecord

NOW = datetime(2026, 10, 18, 4, 0, tzinfo=UTC)
SECRET_TEXT = "這段內文不應出現在錯誤訊息中"


def uid(n: int) -> str:
    return str(uuid.UUID(int=n))


def row(n: int = 1, **overrides: str) -> dict[str, str]:
    base = {
        "item_id": uid(n),
        "source_code": "ncu-announcements",
        "content_hash": f"{n:064x}",
        "published_at": "2026-10-17T08:00:00+00:00",
        "url": f"https://www.ncu.edu.tw/p/406-1000-{n}.php",
        "text": SECRET_TEXT,
        "is_campus_relevant": "y",
        "category": "facility",
        "claim_type": "official",
        "verification": "verified",
        "event_group": f"evt-{n}",
        "related_groups": "",
        "high_risk": "n",
        "mentions_person": "n",
        "tags": "停電;工程五館",
        "confirm": "y",
    }
    base.update(overrides)
    return base


def write_csv(path: Path, rows: list[dict[str, str]], columns: list[str] | None = None) -> Path:
    cols = columns or list(EXPORT_COLUMNS)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=cols)
        writer.writeheader()
        for r in rows:
            writer.writerow({c: r.get(c, "") for c in cols})
    return path


def parse(path: Path) -> list[LabelRecord]:
    return parse_labeled_csv(path, now=NOW)


def rejected(path: Path, *fragments: str) -> str:
    with pytest.raises(LabelImportError) as info:
        parse(path)
    message = str(info.value)
    for fragment in fragments:
        assert fragment in message
    assert SECRET_TEXT not in message
    return message


# --- CSV 匯入 ---------------------------------------------------------------------------


def test_confirmed_rows_become_human_labels(tmp_path: Path) -> None:
    [label] = parse(write_csv(tmp_path / "b.csv", [row()]))
    assert str(label.item_id) == uid(1)
    assert label.category is Category.FACILITY
    assert label.tags == ["停電", "工程五館"]
    assert label.labeler == "human"
    assert label.labeled_at == NOW
    assert label.published_at == datetime(2026, 10, 17, 8, 0, tzinfo=UTC)


def test_unconfirmed_rows_are_skipped(tmp_path: Path) -> None:
    labels = parse(write_csv(tmp_path / "b.csv", [row(1), row(2, confirm="")]))
    assert [str(lbl.item_id) for lbl in labels] == [uid(1)]


@pytest.mark.parametrize("value", ["y", "Y", "yes", "是", "1", "true"])
def test_boolean_spellings(tmp_path: Path, value: str) -> None:
    [label] = parse(write_csv(tmp_path / "b.csv", [row(high_risk=value)]))
    assert label.high_risk is True


def test_blank_flags_default_to_false(tmp_path: Path) -> None:
    [label] = parse(write_csv(tmp_path / "b.csv", [row(high_risk="", mentions_person="")]))
    assert label.high_risk is False
    assert label.mentions_person is False


def test_not_relevant_row_may_leave_event_fields_blank(tmp_path: Path) -> None:
    [label] = parse(
        write_csv(
            tmp_path / "b.csv",
            [row(is_campus_relevant="n", category="", verification="", event_group="", tags="")],
        )
    )
    assert label.category is Category.NONE
    assert label.verification is None
    assert label.event_group is None


def test_related_groups_are_split(tmp_path: Path) -> None:
    [label] = parse(write_csv(tmp_path / "b.csv", [row(related_groups="evt-a; evt-b")]))
    assert label.related_groups == ["evt-a", "evt-b"]


def test_text_and_url_columns_are_not_kept(tmp_path: Path) -> None:
    [label] = parse(write_csv(tmp_path / "b.csv", [row()]))
    dumped = label.model_dump_json()
    assert SECRET_TEXT not in dumped
    assert "url" not in json.loads(dumped)


def test_text_and_url_columns_are_optional(tmp_path: Path) -> None:
    cols = [c for c in EXPORT_COLUMNS if c not in ("text", "url")]
    assert len(parse(write_csv(tmp_path / "b.csv", [row()], cols))) == 1


def test_invalid_row_rejects_whole_file(tmp_path: Path) -> None:
    path = write_csv(tmp_path / "b.csv", [row(1), row(2, category="other")])
    rejected(path, "b.csv", "第 3 列", "category")


def test_inconsistent_row_is_rejected(tmp_path: Path) -> None:
    path = write_csv(tmp_path / "b.csv", [row(category="none", verification="verified")])
    rejected(path, "第 2 列")


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("is_campus_relevant", ""),
        ("is_campus_relevant", "maybe"),
        ("confirm", "ok"),
        ("item_id", "x"),
        ("published_at", "昨天"),
        ("claim_type", ""),
    ],
)
def test_bad_values_are_rejected(tmp_path: Path, column: str, value: str) -> None:
    bad = row()
    bad[column] = value
    rejected(write_csv(tmp_path / "b.csv", [bad]), "第 2 列", column)


def test_duplicate_item_is_rejected(tmp_path: Path) -> None:
    rejected(write_csv(tmp_path / "b.csv", [row(1), row(1)]), "第 3 列", "重複")


def test_missing_column_is_rejected(tmp_path: Path) -> None:
    cols = [c for c in EXPORT_COLUMNS if c != "claim_type"]
    rejected(write_csv(tmp_path / "b.csv", [row()], cols), "claim_type")


def test_unknown_column_is_rejected(tmp_path: Path) -> None:
    """不收自由文字欄（例如自己加的備註），避免個資進入標記檔。"""
    rejected(write_csv(tmp_path / "b.csv", [row()], [*EXPORT_COLUMNS, "notes"]), "notes")


def test_non_utf8_csv_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "b.csv"
    path.write_bytes(",".join(EXPORT_COLUMNS).encode() + "\n停電".encode("big5"))
    rejected(path, "b.csv")


# --- 標記檔 -----------------------------------------------------------------------------


def label(n: int, *, group: str | None = None, days: int = 0, **overrides: Any) -> LabelRecord:
    published = datetime(2026, 9, 1, tzinfo=UTC) + timedelta(days=days)
    data: dict[str, Any] = {
        "schema_version": 1,
        "item_id": uid(n),
        "source_code": "ncu-announcements",
        "content_hash": f"{n:064x}",
        "published_at": published.isoformat(),
        "is_campus_relevant": True,
        "category": "facility" if group else "none",
        "claim_type": "official",
        "verification": "verified" if group else None,
        "event_group": group,
        "related_groups": [],
        "high_risk": False,
        "mentions_person": False,
        "tags": [],
        "labeler": "human",
        "labeled_at": NOW.isoformat(),
    }
    data.update(overrides)
    return LabelRecord.model_validate(data)


def test_missing_labels_file_is_empty(tmp_path: Path) -> None:
    assert load_labels(tmp_path / "labels.jsonl") == {}


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "labeled" / "labels.jsonl"
    records = {lbl.item_id: lbl for lbl in [label(2), label(1, group="evt-a")]}
    save_labels(path, records)
    assert load_labels(path) == records
    lines = path.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["item_id"] for line in lines] == [uid(1), uid(2)]  # 依 ID 排序
    assert not list(path.parent.glob("*.tmp"))


def test_corrupt_labels_file_raises(tmp_path: Path) -> None:
    path = tmp_path / "labels.jsonl"
    path.write_text('{"item_id": "x"}\n', encoding="utf-8")
    with pytest.raises(LabelImportError, match=r"labels\.jsonl"):
        load_labels(path)


def test_merge_new_labels_replace_old(tmp_path: Path) -> None:
    old = {label(1).item_id: label(1), label(2).item_id: label(2)}
    merged, added, updated = merge_labels(old, [label(2, tags=["改過"]), label(3)])
    assert (added, updated) == (1, 1)
    assert merged[label(2).item_id].tags == ["改過"]
    assert len(merged) == 3


# --- 切分與統計 -------------------------------------------------------------------------


def test_split_keeps_event_groups_together_and_latest_groups_in_test() -> None:
    records = [
        label(1, group="evt-a", days=0),
        label(2, group="evt-a", days=30),  # 轉載晚出現，仍跟同一事件
        label(3, group="evt-b", days=5),
        label(4, days=10),
        label(5, days=20),
        label(6, group="evt-c", days=25),
    ]
    split = split_dataset(records, test_ratio=0.4)
    assert split[records[0].item_id] == split[records[1].item_id]
    # 5 組依最早時間排序：evt-a(0)、evt-b(5)、item4(10)、item5(20)、evt-c(25)
    test_ids = {i for i, part in split.items() if part == "test"}
    assert test_ids == {records[4].item_id, records[5].item_id}  # 最晚的 2 組（5 × 0.4）


def test_split_without_published_time_goes_to_dev() -> None:
    records = [label(1, published_at=None), label(2, days=1), label(3, days=2)]
    split = split_dataset(records, test_ratio=0.34)
    assert split[records[0].item_id] == "dev"
    assert split[records[2].item_id] == "test"


def test_split_is_deterministic() -> None:
    records = [label(n, days=n) for n in range(1, 11)]
    assert split_dataset(records) == split_dataset(list(reversed(records)))


@pytest.mark.parametrize("ratio", [0.0, 1.0, -0.1])
def test_invalid_ratio_is_rejected(ratio: float) -> None:
    with pytest.raises(ValueError):
        split_dataset([label(1)], test_ratio=ratio)


def test_write_split_files(tmp_path: Path) -> None:
    records = [label(n, days=n) for n in range(1, 5)]
    split = split_dataset(records, test_ratio=0.5)
    write_split(tmp_path, records, split)
    dev = (tmp_path / "dev.jsonl").read_text(encoding="utf-8").splitlines()
    test = (tmp_path / "test.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(dev) == 2 and len(test) == 2
    assert {json.loads(line)["item_id"] for line in test} == {uid(3), uid(4)}


def test_stats_counts() -> None:
    records = [
        label(1, group="evt-a"),
        label(2, group="evt-a", high_risk=True, source_code="facebook"),
        label(3),
    ]
    result = stats(records, split_dataset(records, test_ratio=0.5))
    assert result.total == 3
    assert result.by_category == {"facility": 2, "none": 1}
    assert result.by_source == {"facebook": 1, "ncu-announcements": 2}
    assert result.high_risk == 1
    assert result.event_groups == 1
    assert result.dev + result.test == 3
