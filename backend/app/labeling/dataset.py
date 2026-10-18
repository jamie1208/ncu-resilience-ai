"""標記匯入、標記檔讀寫、dev／test 切分與統計（P1-T09，ADR-0009）。

- 匯入：讀取使用者填好的 CSV，只收 `confirm = y` 的列；任何一列不合格就整個檔案不匯入。
  錯誤訊息只含檔名、列號與欄位名稱，不含內文（AGENTS.md：日誌不得包含原文）。
- 標記檔：`data/labeled/labels.jsonl`，每行一筆 `LabelRecord`，依 item_id 排序；不含內文。
- 切分：同一個 event_group 的文章一定在同一邊；非事件各自成一組。各組依最早發布時間排序，
  最晚的一部分組進 test，避免同一事件的轉載同時出現在 dev 與 test（規格 §14.1）。
"""

from __future__ import annotations

import csv
import io
import os
import uuid
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from app.labeling.export import EXPORT_COLUMNS, LABEL_COLUMNS
from app.labeling.models import Category, LabelRecord

Split = Literal["dev", "test"]

_REQUIRED_COLUMNS = ("item_id", "source_code", "content_hash", "published_at", *LABEL_COLUMNS)
_OPTIONAL_COLUMNS = ("url", "text", "confirm")
_TRUE = frozenset({"y", "yes", "true", "1", "是"})
_FALSE = frozenset({"n", "no", "false", "0", "否", ""})
_MULTI = ("related_groups", "tags")
_BOOLS = ("high_risk", "mentions_person")


class LabelImportError(ValueError):
    """標記檔或 CSV 不合格；訊息不含內文。"""


# --- CSV 匯入 ---------------------------------------------------------------------------


def _bool(value: str, *, required: bool) -> bool:
    text = value.strip().lower()
    if required and not text:
        raise ValueError("必填")
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise ValueError("只能填 y 或 n")


def _record(row: Mapping[str, str], now: datetime) -> dict[str, Any]:
    """把一列 CSV 轉成 LabelRecord 的輸入；欄位錯誤以 (欄位, 原因) 拋出。"""
    data: dict[str, Any] = {"labeled_at": now, "labeler": "human"}
    for column in ("item_id", "source_code", "content_hash", "claim_type"):
        data[column] = row[column].strip()
    published = row["published_at"].strip()
    try:
        data["published_at"] = datetime.fromisoformat(published) if published else None
    except ValueError:
        raise _ColumnError("published_at", "不是有效的 ISO 8601 時間") from None
    try:
        relevant = _bool(row["is_campus_relevant"], required=True)
    except ValueError as exc:
        raise _ColumnError("is_campus_relevant", str(exc)) from None
    data["is_campus_relevant"] = relevant
    for column in _BOOLS:
        try:
            data[column] = _bool(row[column], required=False)
        except ValueError as exc:
            raise _ColumnError(column, str(exc)) from None
    for column in _MULTI:
        data[column] = [part.strip() for part in row[column].split(";") if part.strip()]
    category = row["category"].strip()
    data["category"] = category or (Category.NONE.value if not relevant else "")
    for column in ("verification", "event_group"):
        data[column] = row[column].strip() or None
    return data


class _ColumnError(Exception):
    def __init__(self, column: str, reason: str) -> None:
        super().__init__(f"{column} {reason}")


def _describe(exc: ValidationError) -> str:
    """只列出欄位與錯誤類型，不帶入輸入值。"""
    parts: list[str] = []
    for error in exc.errors(include_input=False, include_url=False, include_context=False):
        loc = ".".join(str(p) for p in error["loc"] if not isinstance(p, int)) or "（整列）"
        reason = error["msg"] if error["type"] == "value_error" else error["type"]
        parts.append(f"{loc} {reason}")
    return "；".join(parts)


def parse_labeled_csv(path: Path, *, now: datetime) -> list[LabelRecord]:
    """讀取使用者填好的 CSV，回傳 `confirm = y` 的標記；任何一列不合格就拋出 LabelImportError。"""
    name = path.name
    try:
        text = path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError:
        raise LabelImportError(f"{name}：不是 UTF-8 編碼") from None
    reader = csv.DictReader(io.StringIO(text, newline=""))
    header = list(reader.fieldnames or [])
    unknown = [c for c in header if c not in (*_REQUIRED_COLUMNS, *_OPTIONAL_COLUMNS)]
    if unknown:
        shown = [c if len(c) <= 40 else c[:40] + "…" for c in unknown]
        raise LabelImportError(f"{name}：不支援的欄位 {', '.join(shown)}（不收自由文字欄）")
    missing = [c for c in (*_REQUIRED_COLUMNS, "confirm") if c not in header]
    if missing:
        raise LabelImportError(f"{name}：缺少欄位 {', '.join(missing)}")
    labels: list[LabelRecord] = []
    seen: set[uuid.UUID] = set()
    try:
        for line, row in enumerate(reader, start=2):  # 第 1 列是標題
            where = f"{name}：第 {line} 列"
            if None in row or any(value is None for value in row.values()):
                raise LabelImportError(f"{where}的欄位數與標題列不符")
            confirm = row["confirm"].strip().lower()
            if not confirm:
                continue
            if confirm not in _TRUE:
                raise LabelImportError(f"{where}：confirm 只能填 y 或留空")
            try:
                label = LabelRecord.model_validate(_record(row, now))
            except _ColumnError as exc:
                raise LabelImportError(f"{where}：{exc}") from None
            except ValidationError as exc:
                raise LabelImportError(f"{where}：{_describe(exc)}") from None
            if label.item_id in seen:
                raise LabelImportError(f"{where}：item_id 與前面的列重複")
            seen.add(label.item_id)
            labels.append(label)
    except csv.Error:
        raise LabelImportError(f"{name}：不是有效的 CSV") from None
    return labels


# --- 標記檔 -----------------------------------------------------------------------------


def load_labels(path: Path) -> dict[uuid.UUID, LabelRecord]:
    """讀取標記檔；檔案不存在時回傳空的結果，內容不合格時拋出 LabelImportError。"""
    if not path.exists():
        return {}
    labels: dict[uuid.UUID, LabelRecord] = {}
    for line, text in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not text.strip():
            continue
        try:
            label = LabelRecord.model_validate_json(text)
        except ValidationError as exc:
            raise LabelImportError(
                f"{path.name}：第 {line} 行格式錯誤（{_describe(exc)}）"
            ) from None
        labels[label.item_id] = label
    return labels


def _write_jsonl(path: Path, records: Iterable[LabelRecord]) -> None:
    """先寫暫存檔再改名，避免中途失敗留下半個檔案。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("".join(r.model_dump_json() + "\n" for r in records), encoding="utf-8")
    os.replace(tmp, path)


def save_labels(path: Path, labels: Mapping[uuid.UUID, LabelRecord]) -> None:
    """寫出標記檔，依 item_id 排序。"""
    _write_jsonl(path, (labels[k] for k in sorted(labels, key=str)))


def merge_labels(
    existing: Mapping[uuid.UUID, LabelRecord], new: Iterable[LabelRecord]
) -> tuple[dict[uuid.UUID, LabelRecord], int, int]:
    """合併新標記（同一篇以新的為準）；回傳 (合併結果, 新增筆數, 更新筆數)。"""
    merged = dict(existing)
    added = updated = 0
    for label in new:
        if label.item_id in merged:
            updated += 1
        else:
            added += 1
        merged[label.item_id] = label
    return merged, added, updated


# --- 切分與統計 -------------------------------------------------------------------------


def _group_key(label: LabelRecord) -> str:
    return f"event:{label.event_group}" if label.event_group else f"item:{label.item_id}"


def split_dataset(
    labels: Sequence[LabelRecord], *, test_ratio: float = 0.3
) -> dict[uuid.UUID, Split]:
    """依事件分組與時間切分；沒有發布時間的組一律放 dev。結果與輸入順序無關。"""
    if not 0 < test_ratio < 1:
        raise ValueError("test_ratio 必須介於 0 與 1 之間")
    groups: dict[str, list[LabelRecord]] = {}
    for label in labels:
        groups.setdefault(_group_key(label), []).append(label)

    def earliest(key: str) -> tuple[int, datetime | None, str]:
        times = [lbl.published_at for lbl in groups[key] if lbl.published_at is not None]
        return (0, None, key) if not times else (1, min(times), key)

    ordered = sorted(groups, key=lambda k: (earliest(k)[0], earliest(k)[1] or datetime.min, k))
    timed = [k for k in ordered if earliest(k)[0] == 1]
    n_test = max(1, round(len(timed) * test_ratio)) if len(ordered) >= 2 and timed else 0
    test_keys = set(timed[len(timed) - n_test :]) if n_test else set()
    return {
        label.item_id: ("test" if key in test_keys else "dev")
        for key, members in groups.items()
        for label in members
    }


def write_split(
    directory: Path, labels: Sequence[LabelRecord], split: Mapping[uuid.UUID, Split]
) -> None:
    """寫出 `dev.jsonl` 與 `test.jsonl`。"""
    ordered = sorted(labels, key=lambda lbl: str(lbl.item_id))
    for part in ("dev", "test"):
        _write_jsonl(
            directory / f"{part}.jsonl", (lb for lb in ordered if split[lb.item_id] == part)
        )


@dataclass(frozen=True)
class DatasetStats:
    """標記資料集統計（G1-5）。"""

    total: int
    dev: int
    test: int
    event_groups: int
    high_risk: int
    by_category: dict[str, int] = field(default_factory=dict)
    by_source: dict[str, int] = field(default_factory=dict)


def stats(labels: Sequence[LabelRecord], split: Mapping[uuid.UUID, Split]) -> DatasetStats:
    """統計筆數、切分、事件組數、高風險數與各類別、各來源的分布。"""
    parts = Counter(split[lbl.item_id] for lbl in labels)
    return DatasetStats(
        total=len(labels),
        dev=parts["dev"],
        test=parts["test"],
        event_groups=len({lbl.event_group for lbl in labels if lbl.event_group}),
        high_risk=sum(lbl.high_risk for lbl in labels),
        by_category=dict(sorted(Counter(str(lbl.category) for lbl in labels).items())),
        by_source=dict(sorted(Counter(lbl.source_code for lbl in labels).items())),
    )


__all__ = [
    "EXPORT_COLUMNS",
    "DatasetStats",
    "LabelImportError",
    "Split",
    "load_labels",
    "merge_labels",
    "parse_labeled_csv",
    "save_labels",
    "split_dataset",
    "stats",
    "write_split",
]
