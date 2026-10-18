"""人工匯入 Adapter（P1-T04），來源類型 `manual_import`。

讀取來源資料夾中的 JSON／CSV 檔案，轉成 `RawItem`；格式與規則見
`docs/governance/manual-import-format.md`。用於沒有合法機器存取管道的來源（規格 §16），
例如 P1-T13 的 Facebook 社團每日擷取（ADR-0008）。

- 資料夾位於 repo 的 `data/` 底下（容器內為 `/data`），不可用絕對路徑、`..` 或符號連結跳出。
- 檔案依檔名排序；cursor 是上一輪處理到的最後一個檔名，只讀檔名更後面的檔案。
- 只接受格式文件列出的欄位：出現任何其他欄位（特別是發文者等個資欄位）時，整個檔案拒絕匯入。
- 任何一筆不合格就拋出不可重試的 `ImportFormatError`，這一輪不推進 cursor；
  錯誤訊息只含檔名、第幾筆與欄位名稱，不含內文或欄位值（AGENTS.md：日誌不得包含原文與個資）。

來源設定（`SourceConfig.options`）：
- `directory`（必填）：相對於 `data/` 的資料夾，例如 `raw/facebook`
- `max_files`：每輪最多讀幾個新檔案（1–366，預設 31）
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.ingestion.base import AdapterError
from app.ingestion.normalize import to_utc
from app.ingestion.registry import registry
from app.ingestion.types import RawItem, SourceConfig

DATA_ROOT = Path(__file__).resolve().parents[4] / "data"
"""repo 根目錄的 data/（容器內為 /data）。"""

SUFFIXES = frozenset({".json", ".csv"})
MAX_FILE_BYTES = 5_000_000
MAX_ITEMS_PER_FILE = 1_000
MAX_CONTENT_CHARS = 50_000
MAX_TITLE_CHARS = 300
MAX_EXTERNAL_ID_CHARS = 255  # source_items.external_id 的欄位長度
CLOCK_SKEW = timedelta(minutes=5)

PERSONAL_FIELDS = frozenset(
    {
        # 發文者
        "author",
        "author_name",
        "author_id",
        "author_url",
        "poster",
        "user",
        "user_id",
        "username",
        "name",
        "nickname",
        "account",
        "profile",
        "profile_url",
        "avatar",
        "avatar_url",
        # 互動者
        "comments",
        "commenters",
        "reactions",
        "likes",
        # 聯絡方式
        "email",
        "phone",
    }
)
"""ADR-0008 核准條件：與人有關的欄位一律不收；出現時給出明確的錯誤原因。"""


class ImportFormatError(AdapterError):
    """匯入檔案不符合格式；重試無效，需要人工修正或移走檔案。"""

    retryable = False


class ManualImportOptions(BaseModel):
    """人工匯入來源的設定；無效值在建立 Adapter 時拋出 ValueError。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    directory: str = Field(min_length=1)
    max_files: int = Field(default=31, ge=1, le=366)

    @field_validator("directory")
    @classmethod
    def _inside_data_root(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or not value.strip():
            raise ValueError("directory 必須是 data/ 底下的相對路徑")
        return value


class ImportedItem(BaseModel):
    """匯入檔案中的一筆項目；欄位採嚴格型別，多出的欄位一律拒絕。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    url: str
    content: str = Field(max_length=MAX_CONTENT_CHARS)
    published_at: datetime
    fetched_at: datetime
    title: str | None = Field(default=None, max_length=MAX_TITLE_CHARS)
    external_id: str | None = Field(default=None, min_length=1, max_length=MAX_EXTERNAL_ID_CHARS)

    @field_validator("url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise ValueError("必須是 http(s) 絕對網址")
        return value

    @field_validator("content")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("不可空白")
        return value

    @field_validator("published_at", "fetched_at", mode="before")
    @classmethod
    def _iso_datetime(cls, value: object) -> datetime:
        if not isinstance(value, str):
            raise ValueError("必須是 ISO 8601 字串")
        try:
            return to_utc(datetime.fromisoformat(value.strip()))
        except ValueError:
            raise ValueError("不是有效的 ISO 8601 時間") from None


class _JsonFile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    format_version: Literal[1]
    source_code: str
    items: list[dict[str, Any]] = Field(max_length=MAX_ITEMS_PER_FILE)


_CSV_REQUIRED = ("source_code", "url", "published_at", "fetched_at", "content")
_CSV_OPTIONAL = ("title", "external_id")


def _utc_now() -> datetime:
    return datetime.now(UTC)


@registry.register("manual_import")
class ManualImportAdapter:
    """人工匯入 Adapter。"""

    code: str

    def __init__(
        self,
        config: SourceConfig,
        *,
        data_root: Path = DATA_ROOT,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.code = config.code
        self._options = ManualImportOptions.model_validate(dict(config.options))
        self._data_root = data_root
        self._clock = clock
        self._last_file: str | None = None

    async def fetch_since(self, cursor: str | None) -> list[RawItem]:
        """讀取檔名排在 `cursor` 之後的檔案（最多 `max_files` 個），依檔名順序回傳項目。

        同一篇（external_id，省略時為 url）出現在多個檔案時，以檔名較後面的為準。
        """
        self._last_file = None
        folder = self._folder()
        names = sorted(
            entry.name
            for entry in folder.iterdir()
            if entry.suffix in SUFFIXES
            and not entry.name.startswith(".")
            and not entry.is_symlink()
            and entry.is_file()
            and (cursor is None or entry.name > cursor)
        )[: self._options.max_files]
        now = self._clock()
        merged: dict[str, RawItem] = {}
        for name in names:
            for raw in self._read_file(folder / name, now):
                key = raw.external_id or raw.url
                merged.pop(key, None)  # 重新插入，讓順序跟著較後面的檔案
                merged[key] = raw
        if names:
            self._last_file = names[-1]
        return list(merged.values())

    def next_cursor(self, items: Sequence[RawItem], previous: str | None) -> str | None:
        """回傳本輪處理到的最後一個檔名；沒有讀到新檔案時回傳 previous。"""
        return self._last_file or previous

    # --- 檔案 ---------------------------------------------------------------------------

    def _folder(self) -> Path:
        root = self._data_root.resolve()
        folder = (root / self._options.directory).resolve()
        if not folder.is_relative_to(root):
            raise AdapterError(f"來源 {self.code} 的資料夾不在 data/ 底下")
        if not folder.is_dir():
            raise AdapterError(f"來源 {self.code} 的資料夾 {self._options.directory} 不存在")
        return folder

    def _read_file(self, path: Path, now: datetime) -> list[RawItem]:
        name = path.name
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ImportFormatError(f"{name}：檔案超過 {MAX_FILE_BYTES:,} bytes")
        try:
            text = path.read_bytes().decode("utf-8-sig")
        except UnicodeDecodeError:
            raise ImportFormatError(f"{name}：不是 UTF-8 編碼") from None
        rows = self._json_rows(name, text) if path.suffix == ".json" else self._csv_rows(name, text)
        if len(rows) > MAX_ITEMS_PER_FILE:
            raise ImportFormatError(f"{name}：超過 {MAX_ITEMS_PER_FILE:,} 筆")
        items: list[RawItem] = []
        seen: set[str] = set()
        for index, row in enumerate(rows, start=1):
            raw = self._item(name, index, row, now)
            key = raw.external_id or raw.url
            if key in seen:
                raise ImportFormatError(f"{name}：第 {index} 筆與前面的項目重複（external_id）")
            seen.add(key)
            items.append(raw)
        return items

    def _json_rows(self, name: str, text: str) -> list[dict[str, Any]]:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ImportFormatError(f"{name}：不是有效的 JSON（第 {exc.lineno} 行）") from None
        if isinstance(data, dict):
            unknown = sorted(set(data) - set(_JsonFile.model_fields))
            if unknown:
                raise ImportFormatError(f"{name}：不支援的欄位 {', '.join(unknown)}")
        try:
            parsed = _JsonFile.model_validate(data)
        except ValidationError as exc:
            raise ImportFormatError(f"{name}：檔案結構錯誤（{_describe(exc)}）") from None
        self._check_source(name, None, parsed.source_code)
        return parsed.items

    def _csv_rows(self, name: str, text: str) -> list[dict[str, Any]]:
        reader = csv.DictReader(io.StringIO(text, newline=""))
        header = reader.fieldnames or []
        personal = sorted(PERSONAL_FIELDS & set(header))
        if personal:
            raise ImportFormatError(f"{name}：含個資欄位 {', '.join(personal)}（ADR-0008 不收）")
        unknown = [h for h in header if h not in (*_CSV_REQUIRED, *_CSV_OPTIONAL)]
        if unknown:
            raise ImportFormatError(f"{name}：不支援的欄位 {', '.join(_field_list(unknown))}")
        missing = [h for h in _CSV_REQUIRED if h not in header]
        if missing:
            raise ImportFormatError(f"{name}：缺少欄位 {', '.join(missing)}")
        rows: list[dict[str, Any]] = []
        try:
            for index, row in enumerate(reader, start=1):
                if None in row or any(value is None for value in row.values()):
                    raise ImportFormatError(f"{name}：第 {index} 筆的欄位數與標題列不符")
                self._check_source(name, index, row.pop("source_code"))
                # CSV 沒有 null：選填欄位的空字串視為沒有
                rows.append({k: v for k, v in row.items() if not (k in _CSV_OPTIONAL and v == "")})
                if len(rows) > MAX_ITEMS_PER_FILE:
                    break
        except csv.Error:
            raise ImportFormatError(f"{name}：不是有效的 CSV") from None
        return rows

    def _check_source(self, name: str, index: int | None, source_code: object) -> None:
        if source_code != self.code:
            where = f"第 {index} 筆的 " if index is not None else ""
            raise ImportFormatError(f"{name}：{where}source_code 與來源 {self.code} 不符")

    def _item(self, name: str, index: int, row: object, now: datetime) -> RawItem:
        where = f"{name}：第 {index} 筆"
        if not isinstance(row, dict):
            raise ImportFormatError(f"{where}不是物件")
        personal = sorted(PERSONAL_FIELDS & set(row))
        if personal:
            raise ImportFormatError(f"{where}含個資欄位 {', '.join(personal)}（ADR-0008 不收）")
        unknown = sorted(set(row) - set(ImportedItem.model_fields))
        if unknown:
            raise ImportFormatError(f"{where}含不支援的欄位 {', '.join(_field_list(unknown))}")
        try:
            parsed = ImportedItem.model_validate(row)
        except ValidationError as exc:
            raise ImportFormatError(f"{where}：{_describe(exc)}") from None
        if parsed.external_id is None and len(parsed.url) > MAX_EXTERNAL_ID_CHARS:
            raise ImportFormatError(
                f"{where}：url 超過 {MAX_EXTERNAL_ID_CHARS} 字元，需要另外提供 external_id"
            )
        if parsed.fetched_at > now + CLOCK_SKEW:
            raise ImportFormatError(f"{where}：fetched_at 晚於現在")
        if parsed.published_at > parsed.fetched_at + CLOCK_SKEW:
            raise ImportFormatError(f"{where}：published_at 晚於 fetched_at")
        return RawItem(
            source_code=self.code,
            external_id=parsed.external_id or parsed.url,
            url=parsed.url,
            title=parsed.title,
            content=parsed.content,
            published_at=parsed.published_at,
            fetched_at=parsed.fetched_at,
        )


def _describe(exc: ValidationError) -> str:
    """只列出欄位位置與錯誤類型，不帶入輸入值。"""
    parts: list[str] = []
    for error in exc.errors(include_input=False, include_url=False, include_context=False):
        loc = ".".join(str(p) for p in error["loc"]) or "（整體）"
        reason = error["msg"] if error["type"] == "value_error" else error["type"]
        parts.append(f"{loc} {reason}")
    return "；".join(parts)


def _field_list(fields: Sequence[str]) -> list[str]:
    """欄位名稱來自外部檔案：過長時截斷，避免把內容當成欄位名稱帶進錯誤訊息。"""
    return [f if len(f) <= 40 else f[:40] + "…" for f in fields]
