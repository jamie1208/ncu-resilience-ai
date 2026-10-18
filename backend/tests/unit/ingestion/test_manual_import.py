"""P1-T04：人工匯入 Adapter（JSON／CSV；格式見 docs/governance/manual-import-format.md）。"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.ingestion.adapters.manual_import import (
    ImportFormatError,
    ManualImportAdapter,
)
from app.ingestion.base import AdapterError, SourceAdapter
from app.ingestion.registry import registry
from app.ingestion.types import AccessPolicy, SourceConfig

FORMAT_DOC = Path(__file__).resolve().parents[4] / "docs" / "governance" / "manual-import-format.md"
NOW = datetime(2026, 10, 19, 0, 10, tzinfo=UTC)  # 台北時間 08:10
SECRET_TEXT = "這段內文不應出現在錯誤訊息中"

pytestmark = pytest.mark.anyio


def item(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "url": "https://www.facebook.com/groups/example/posts/1",
        "published_at": "2026-10-18T07:42:00+08:00",
        "fetched_at": "2026-10-19T08:03:00+08:00",
        "content": "工程五館今天下午停電。",
    }
    base.update(overrides)
    return base


def payload(*items: dict[str, Any], **top: Any) -> dict[str, Any]:
    return {"format_version": 1, "source_code": "facebook", "items": list(items), **top}


class Inbox:
    """tmp_path 下的 data/ 根目錄與來源資料夾。"""

    def __init__(self, root: Path) -> None:
        self.data_root = root / "data"
        self.folder = self.data_root / "raw" / "facebook"
        self.folder.mkdir(parents=True)

    def write_json(self, name: str, data: Any) -> Path:
        path = self.folder / name
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return path

    def write_text(self, name: str, text: str, encoding: str = "utf-8") -> Path:
        path = self.folder / name
        path.write_text(text, encoding=encoding)
        return path

    def adapter(self, **options: Any) -> ManualImportAdapter:
        return ManualImportAdapter(config(**options), data_root=self.data_root, clock=lambda: NOW)


def config(**options: Any) -> SourceConfig:
    return SourceConfig(
        code="facebook",
        type="manual_import",
        access_policy=AccessPolicy.APPROVED,
        enabled=True,
        options={"directory": "raw/facebook", **options},
    )


@pytest.fixture
def inbox(tmp_path: Path) -> Inbox:
    return Inbox(tmp_path)


async def assert_rejected(adapter: ManualImportAdapter, *fragments: str) -> ImportFormatError:
    with pytest.raises(ImportFormatError) as info:
        await adapter.fetch_since(None)
    message = str(info.value)
    for fragment in fragments:
        assert fragment in message
    assert SECRET_TEXT not in message
    assert info.value.retryable is False
    return info.value


# --- 註冊與設定 -------------------------------------------------------------------------


def test_registered_as_manual_import_type() -> None:
    adapter = registry.create(config())
    assert isinstance(adapter, ManualImportAdapter)
    assert isinstance(adapter, SourceAdapter)
    assert adapter.code == "facebook"


def test_import_format_error_is_not_retryable() -> None:
    assert issubclass(ImportFormatError, AdapterError)
    assert ImportFormatError.retryable is False


@pytest.mark.parametrize(
    "options",
    [
        {"directory": ""},
        {"directory": "/etc"},
        {"directory": "../secrets"},
        {"directory": "raw/../../outside"},
        {"max_files": 0},
        {"max_files": 367},
        {"unknown": 1},
    ],
)
def test_invalid_options_are_rejected(options: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        ManualImportAdapter(config(**options))


def test_directory_is_required() -> None:
    bare = SourceConfig(
        code="facebook", type="manual_import", access_policy=AccessPolicy.APPROVED, enabled=True
    )
    with pytest.raises(ValueError):
        ManualImportAdapter(bare)


async def test_missing_directory_raises_non_retryable_error(tmp_path: Path) -> None:
    adapter = ManualImportAdapter(config(), data_root=tmp_path / "data", clock=lambda: NOW)
    with pytest.raises(AdapterError) as info:
        await adapter.fetch_since(None)
    assert info.value.retryable is False


# --- JSON -------------------------------------------------------------------------------


async def test_json_item_fields(inbox: Inbox) -> None:
    inbox.write_json("2026-10-19.json", payload(item(title="停電")))
    [raw] = await inbox.adapter().fetch_since(None)
    assert raw.source_code == "facebook"
    assert raw.url == "https://www.facebook.com/groups/example/posts/1"
    assert raw.external_id == raw.url  # 省略時以 url 代替
    assert raw.title == "停電"
    assert raw.content == "工程五館今天下午停電。"
    assert raw.content_type == "text/plain"
    assert raw.published_at == datetime(2026, 10, 17, 23, 42, tzinfo=UTC)
    assert raw.fetched_at == datetime(2026, 10, 19, 0, 3, tzinfo=UTC)


async def test_explicit_external_id_is_kept(inbox: Inbox) -> None:
    inbox.write_json("2026-10-19.json", payload(item(external_id="post-1")))
    [raw] = await inbox.adapter().fetch_since(None)
    assert raw.external_id == "post-1"


async def test_naive_times_are_taipei(inbox: Inbox) -> None:
    inbox.write_json(
        "2026-10-19.json",
        payload(item(published_at="2026-10-18 21:15", fetched_at="2026-10-19T08:03:00")),
    )
    [raw] = await inbox.adapter().fetch_since(None)
    assert raw.published_at == datetime(2026, 10, 18, 13, 15, tzinfo=UTC)
    assert raw.fetched_at == datetime(2026, 10, 19, 0, 3, tzinfo=UTC)


async def test_empty_items_is_valid(inbox: Inbox) -> None:
    inbox.write_json("2026-10-19.json", payload())
    adapter = inbox.adapter()
    assert await adapter.fetch_since(None) == []
    assert adapter.next_cursor([], None) == "2026-10-19.json"


async def test_doc_example_is_valid(inbox: Inbox) -> None:
    """格式文件中的 JSON 範例必須能匯入，避免文件與程式不一致。"""
    doc = FORMAT_DOC.read_text(encoding="utf-8")
    example = re.search(r"```json\n(.*?)```", doc, re.S)
    assert example is not None
    inbox.write_text("2026-10-19.json", example.group(1))
    items = await inbox.adapter().fetch_since(None)
    assert len(items) == 2


# --- CSV --------------------------------------------------------------------------------


CSV_HEADER = "source_code,url,published_at,fetched_at,title,content\n"


async def test_csv_rows(inbox: Inbox) -> None:
    inbox.write_text(
        "2026-10-19.csv",
        CSV_HEADER
        + "facebook,https://www.facebook.com/groups/example/posts/1,"
        + "2026-10-18T07:42:00+08:00,2026-10-19T08:03:00+08:00,,工程五館停電\n"
        + "facebook,https://www.facebook.com/groups/example/posts/2,"
        + '2026-10-18 21:15,2026-10-19 08:03,宵夜街,"後門機車格,被拖吊"\n',
    )
    first, second = await inbox.adapter().fetch_since(None)
    assert first.title is None  # 空字串視為沒有
    assert first.content == "工程五館停電"
    assert second.title == "宵夜街"
    assert second.content == "後門機車格,被拖吊"
    assert second.published_at == datetime(2026, 10, 18, 13, 15, tzinfo=UTC)


async def test_csv_with_excel_bom(inbox: Inbox) -> None:
    inbox.write_text(
        "2026-10-19.csv",
        CSV_HEADER
        + "facebook,https://www.facebook.com/groups/example/posts/1,"
        + "2026-10-18T07:42:00+08:00,2026-10-19T08:03:00+08:00,,內文\n",
        encoding="utf-8-sig",
    )
    [raw] = await inbox.adapter().fetch_since(None)
    assert raw.source_code == "facebook"


async def test_csv_personal_column_is_rejected(inbox: Inbox) -> None:
    inbox.write_text(
        "2026-10-19.csv",
        "source_code,url,published_at,fetched_at,content,author\n"
        + "facebook,https://www.facebook.com/groups/example/posts/1,"
        + f"2026-10-18T07:42:00+08:00,2026-10-19T08:03:00+08:00,{SECRET_TEXT},王小明\n",
    )
    error = await assert_rejected(inbox.adapter(), "2026-10-19.csv", "author")
    assert "王小明" not in str(error)


async def test_csv_source_code_mismatch_is_rejected(inbox: Inbox) -> None:
    inbox.write_text(
        "2026-10-19.csv",
        CSV_HEADER
        + "dcard,https://www.dcard.tw/f/ncu/p/1,"
        + f"2026-10-18T07:42:00+08:00,2026-10-19T08:03:00+08:00,,{SECRET_TEXT}\n",
    )
    await assert_rejected(inbox.adapter(), "2026-10-19.csv", "source_code")


# --- 拒絕整個檔案 -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    ["author", "author_name", "username", "profile_url", "avatar", "commenters", "email"],
)
async def test_personal_fields_reject_whole_file(inbox: Inbox, field: str) -> None:
    inbox.write_json(
        "2026-10-19.json",
        payload(
            item(),
            item(url="https://www.facebook.com/x/2", content=SECRET_TEXT, **{field: "王小明"}),
        ),
    )
    error = await assert_rejected(inbox.adapter(), "2026-10-19.json", field, "第 2 筆")
    assert "王小明" not in str(error)


async def test_unknown_item_field_is_rejected(inbox: Inbox) -> None:
    inbox.write_json("2026-10-19.json", payload(item(content=SECRET_TEXT, image="a.jpg")))
    await assert_rejected(inbox.adapter(), "2026-10-19.json", "image")


async def test_unknown_top_level_field_is_rejected(inbox: Inbox) -> None:
    inbox.write_json("2026-10-19.json", payload(item(), group_admin="王小明"))
    await assert_rejected(inbox.adapter(), "group_admin")


@pytest.mark.parametrize("field", ["url", "content", "published_at", "fetched_at"])
async def test_missing_required_field_is_rejected(inbox: Inbox, field: str) -> None:
    data = item(content=SECRET_TEXT)
    del data[field]
    inbox.write_json("2026-10-19.json", payload(data))
    await assert_rejected(inbox.adapter(), "2026-10-19.json", "第 1 筆", field)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("url", "javascript:alert(1)"),
        ("url", "/groups/example/posts/1"),
        ("content", "   "),
        ("content", "長" * 50_001),
        ("published_at", "昨天"),
        ("published_at", "2026-13-01T00:00:00"),
        ("fetched_at", 1729296000),
        ("title", 123),
        ("title", "標" * 301),
        ("external_id", ""),
        ("external_id", "x" * 256),
    ],
)
async def test_invalid_values_are_rejected(inbox: Inbox, field: str, value: Any) -> None:
    inbox.write_json("2026-10-19.json", payload(item(**{field: value})))
    await assert_rejected(inbox.adapter(), "第 1 筆", field)


async def test_long_url_needs_external_id(inbox: Inbox) -> None:
    """external_id 欄位上限 255 字元；網址太長時要另外提供 external_id。"""
    long_url = "https://www.facebook.com/groups/example/posts/1?" + "a" * 250
    inbox.write_json("2026-10-19.json", payload(item(url=long_url)))
    await assert_rejected(inbox.adapter(), "第 1 筆", "external_id")
    inbox.write_json("2026-10-20.json", payload(item(url=long_url, external_id="post-1")))
    [raw] = await inbox.adapter().fetch_since("2026-10-19.json")
    assert raw.url == long_url


async def test_fetched_at_in_the_future_is_rejected(inbox: Inbox) -> None:
    inbox.write_json("2026-10-19.json", payload(item(fetched_at="2026-10-19T08:20:00+08:00")))
    await assert_rejected(inbox.adapter(), "fetched_at")


async def test_published_after_fetched_is_rejected(inbox: Inbox) -> None:
    inbox.write_json(
        "2026-10-19.json",
        payload(
            item(published_at="2026-10-19T08:10:00+08:00", fetched_at="2026-10-19T08:03:00+08:00")
        ),
    )
    await assert_rejected(inbox.adapter(), "published_at")


async def test_small_clock_skew_is_allowed(inbox: Inbox) -> None:
    inbox.write_json(
        "2026-10-19.json",
        payload(
            item(published_at="2026-10-19T08:06:00+08:00", fetched_at="2026-10-19T08:14:00+08:00")
        ),
    )
    assert len(await inbox.adapter().fetch_since(None)) == 1


async def test_duplicate_external_id_in_one_file_is_rejected(inbox: Inbox) -> None:
    inbox.write_json("2026-10-19.json", payload(item(), item(content=SECRET_TEXT)))
    await assert_rejected(inbox.adapter(), "2026-10-19.json", "第 2 筆", "重複")


@pytest.mark.parametrize(
    "data",
    [
        payload(source_code="dcard"),
        payload(format_version=2),
        {"source_code": "facebook", "items": []},
        {"format_version": 1, "source_code": "facebook", "items": {}},
        [item()],
    ],
)
async def test_invalid_file_structure_is_rejected(inbox: Inbox, data: Any) -> None:
    inbox.write_json("2026-10-19.json", data)
    await assert_rejected(inbox.adapter(), "2026-10-19.json")


async def test_broken_json_is_rejected(inbox: Inbox) -> None:
    inbox.write_text("2026-10-19.json", '{"format_version": 1, "items": [' + SECRET_TEXT)
    await assert_rejected(inbox.adapter(), "2026-10-19.json")


async def test_non_utf8_file_is_rejected(inbox: Inbox) -> None:
    inbox.write_text("2026-10-19.csv", CSV_HEADER + "facebook,停電\n", encoding="big5")
    await assert_rejected(inbox.adapter(), "2026-10-19.csv")


async def test_oversized_file_is_rejected(inbox: Inbox) -> None:
    inbox.write_text("2026-10-19.json", " " * 5_000_001)
    await assert_rejected(inbox.adapter(), "2026-10-19.json")


async def test_too_many_items_is_rejected(inbox: Inbox) -> None:
    items = [item(url=f"https://www.facebook.com/x/{i}") for i in range(1_001)]
    inbox.write_json("2026-10-19.json", payload(*items))
    await assert_rejected(inbox.adapter(), "2026-10-19.json")


async def test_bad_file_blocks_later_files_until_fixed(inbox: Inbox) -> None:
    inbox.write_json("2026-10-18.json", payload(item(author="王小明")))
    inbox.write_json("2026-10-19.json", payload(item(url="https://www.facebook.com/x/2")))
    adapter = inbox.adapter()
    await assert_rejected(adapter, "2026-10-18.json")
    inbox.write_json("2026-10-18.json", payload(item()))  # 修好之後
    items = await adapter.fetch_since(None)
    assert len(items) == 2


# --- 檔案選擇與 cursor ------------------------------------------------------------------


async def test_files_are_read_in_name_order(inbox: Inbox) -> None:
    inbox.write_json("2026-10-19.json", payload(item(url="https://www.facebook.com/x/19")))
    inbox.write_json("2026-10-18.json", payload(item(url="https://www.facebook.com/x/18")))
    adapter = inbox.adapter()
    items = await adapter.fetch_since(None)
    assert [i.url for i in items] == [
        "https://www.facebook.com/x/18",
        "https://www.facebook.com/x/19",
    ]
    assert adapter.next_cursor(items, None) == "2026-10-19.json"


async def test_cursor_skips_processed_files(inbox: Inbox) -> None:
    inbox.write_json("2026-10-18.json", payload(item(url="https://www.facebook.com/x/18")))
    inbox.write_json("2026-10-19.json", payload(item(url="https://www.facebook.com/x/19")))
    adapter = inbox.adapter()
    items = await adapter.fetch_since("2026-10-18.json")
    assert [i.url for i in items] == ["https://www.facebook.com/x/19"]


async def test_no_new_files_keeps_previous_cursor(inbox: Inbox) -> None:
    inbox.write_json("2026-10-18.json", payload(item()))
    adapter = inbox.adapter()
    assert await adapter.fetch_since("2026-10-18.json") == []
    assert adapter.next_cursor([], "2026-10-18.json") == "2026-10-18.json"


async def test_max_files_limits_one_run(inbox: Inbox) -> None:
    for day in (17, 18, 19):
        inbox.write_json(
            f"2026-10-{day}.json", payload(item(url=f"https://www.facebook.com/x/{day}"))
        )
    adapter = inbox.adapter(max_files=2)
    items = await adapter.fetch_since(None)
    assert len(items) == 2
    cursor = adapter.next_cursor(items, None)
    assert cursor == "2026-10-18.json"
    rest = await adapter.fetch_since(cursor)
    assert [i.url for i in rest] == ["https://www.facebook.com/x/19"]


async def test_same_post_in_two_files_keeps_later_file(inbox: Inbox) -> None:
    inbox.write_json("2026-10-18.json", payload(item(content="舊版內文")))
    inbox.write_json("2026-10-19.json", payload(item(content="新版內文")))
    [raw] = await inbox.adapter().fetch_since(None)
    assert raw.content == "新版內文"


async def test_other_entries_are_ignored(inbox: Inbox, tmp_path: Path) -> None:
    inbox.write_json("2026-10-19.json", payload(item()))
    inbox.write_json(".hidden.json", payload(item(author="x")))
    inbox.write_json("2026-10-20.json.tmp", payload(item(author="x")))
    inbox.write_text("README.md", "說明")
    (inbox.folder / "sub.json").mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps(payload(item(author="x"))), encoding="utf-8")
    os.symlink(outside, inbox.folder / "2026-10-21.json")
    adapter = inbox.adapter()
    items = await adapter.fetch_since(None)
    assert len(items) == 1
    assert adapter.next_cursor(items, None) == "2026-10-19.json"


async def test_symlinked_directory_outside_data_root_is_rejected(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    (data_root / "raw").mkdir(parents=True)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    os.symlink(outside, data_root / "raw" / "facebook")
    adapter = ManualImportAdapter(config(), data_root=data_root, clock=lambda: NOW)
    with pytest.raises(AdapterError) as info:
        await adapter.fetch_since(None)
    assert info.value.retryable is False
