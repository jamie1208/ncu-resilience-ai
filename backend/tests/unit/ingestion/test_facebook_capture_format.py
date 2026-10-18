"""P1-T13：Facebook 每日擷取的輸出格式、設定與排程範本（不連 Facebook、不執行 claude）。

- 範例輸出必須能被 manual_import 匯入；含發文者欄位時被拒絕。
- 擷取 skill 的範例 JSON 也要能匯入，避免 skill 與匯入格式不一致。
- config/sources.yaml 的 facebook 來源。
- launchd 範本的關鍵參數（Sonnet、08:00、只允許 Chrome 工具）。
"""

from __future__ import annotations

import json
import plistlib
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.ingestion.adapters.manual_import import (
    ImportFormatError,
    ManualImportAdapter,
    content_external_id,
)
from app.ingestion.source_settings import build_config, load_source_settings

REPO = Path(__file__).resolve().parents[4]
SAMPLE = REPO / "backend" / "tests" / "fixtures" / "facebook" / "sample_capture.json"
SKILL = REPO / ".claude" / "skills" / "ncu-fb-capture" / "SKILL.md"
PLIST = REPO / "infra" / "launchd" / "tw.ncu-resilience.fb-capture.plist.template"
INSTALL = REPO / "scripts" / "facebook" / "install_schedule.sh"
GROUP_URL = "https://www.facebook.com/groups/NCUgroup"
NOW = datetime(2026, 10, 19, 0, 10, tzinfo=UTC)  # 台北 08:10

pytestmark = pytest.mark.anyio


def adapter(data_root: Path) -> ManualImportAdapter:
    settings = load_source_settings()["facebook"]
    config = build_config("facebook", settings, access_policy="approved", enabled=True)
    return ManualImportAdapter(config, data_root=data_root, clock=lambda: NOW)


def inbox(tmp_path: Path) -> Path:
    folder = tmp_path / "data" / "raw" / "facebook"
    folder.mkdir(parents=True)
    return folder


# --- 設定 -------------------------------------------------------------------------------


def test_facebook_source_is_registered_as_manual_import() -> None:
    settings = load_source_settings()["facebook"]
    assert settings.type == "manual_import"
    assert settings.options["directory"] == "raw/facebook"
    assert settings.options["source_url"] == GROUP_URL  # 擷取 skill 讀取的社團網址，不帶追蹤參數
    assert settings.options["derive_external_id"] is True  # ADR-0011：以內文雜湊識別


# --- 內文雜湊識別（ADR-0011） ------------------------------------------------------------


def test_content_id_ignores_whitespace_and_text_after_300_chars() -> None:
    base = "工程五館 三樓\n跳電" + "電" * 400
    assert content_external_id(base) == content_external_id("  工程五館   三樓 跳電" + "電" * 400)
    assert content_external_id(base) == content_external_id(base + "（已恢復）")  # 300 字之後的修改
    assert content_external_id(base) != content_external_id("宿舍" + base)
    assert re.fullmatch(r"content-[0-9a-f]{40}", content_external_id(base))


async def test_duplicate_content_in_one_capture_is_kept_once(tmp_path: Path) -> None:
    data = json.loads(SAMPLE.read_text(encoding="utf-8"))
    data["items"].append(dict(data["items"][0], published_at="2026-10-18T21:41:00+08:00"))
    (inbox(tmp_path) / "2026-10-19-0805.json").write_text(
        json.dumps(data, ensure_ascii=False), "utf-8"
    )
    items = await adapter(tmp_path / "data").fetch_since(None)
    assert len(items) == 2


async def test_same_post_in_next_day_capture_has_same_id(tmp_path: Path) -> None:
    folder = inbox(tmp_path)
    shutil.copy(SAMPLE, folder / "2026-10-19-0805.json")
    later = json.loads(SAMPLE.read_text(encoding="utf-8"))
    for it in later["items"]:  # 相對時間換算有誤差，但內文相同
        it["published_at"] = "2026-10-18T22:00:00+08:00"
    (folder / "2026-10-20-0805.json").write_text(json.dumps(later, ensure_ascii=False), "utf-8")
    a = adapter(tmp_path / "data")
    first = await a.fetch_since(None)
    assert len({i.external_id for i in first}) == 2  # 兩天的檔案合併後仍是 2 篇


async def test_explicit_external_id_is_still_used(tmp_path: Path) -> None:
    data = json.loads(SAMPLE.read_text(encoding="utf-8"))
    data["items"][0]["external_id"] = "manual-1"
    (inbox(tmp_path) / "2026-10-19-0805.json").write_text(
        json.dumps(data, ensure_ascii=False), "utf-8"
    )
    items = await adapter(tmp_path / "data").fetch_since(None)
    assert items[0].external_id == "manual-1"
    assert (items[1].external_id or "").startswith("content-")


# --- 範例輸出 -----------------------------------------------------------------------------


async def test_sample_capture_is_importable(tmp_path: Path) -> None:
    shutil.copy(SAMPLE, inbox(tmp_path) / "2026-10-19.json")
    items = await adapter(tmp_path / "data").fetch_since(None)
    contents = [it["content"] for it in json.loads(SAMPLE.read_text(encoding="utf-8"))["items"]]
    assert [i.external_id for i in items] == [content_external_id(c) for c in contents]
    assert all(i.source_code == "facebook" and i.url == GROUP_URL for i in items)


async def test_importing_twice_yields_no_new_files(tmp_path: Path) -> None:
    shutil.copy(SAMPLE, inbox(tmp_path) / "2026-10-19.json")
    a = adapter(tmp_path / "data")
    items = await a.fetch_since(None)
    cursor = a.next_cursor(items, None)
    assert await a.fetch_since(cursor) == []


@pytest.mark.parametrize("field", ["author", "author_name", "profile_url", "avatar", "commenters"])
async def test_capture_with_person_fields_is_rejected(tmp_path: Path, field: str) -> None:
    data = json.loads(SAMPLE.read_text(encoding="utf-8"))
    data["items"][0][field] = "（虛構）王小明"
    (inbox(tmp_path) / "2026-10-19.json").write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    with pytest.raises(ImportFormatError) as info:
        await adapter(tmp_path / "data").fetch_since(None)
    assert field in str(info.value)
    assert "王小明" not in str(info.value)


async def test_skill_example_output_is_importable(tmp_path: Path) -> None:
    """SKILL.md 中的輸出範例必須符合匯入格式。"""
    example = re.search(r"```json\n(.*?)```", SKILL.read_text(encoding="utf-8"), re.S)
    assert example is not None
    (inbox(tmp_path) / "2026-10-19.json").write_text(example.group(1), encoding="utf-8")
    items = await adapter(tmp_path / "data").fetch_since(None)
    assert items
    assert all(i.url == GROUP_URL and (i.external_id or "").startswith("content-") for i in items)


# --- skill 規範 ---------------------------------------------------------------------------


def test_skill_forbids_interaction_and_person_data() -> None:
    text = SKILL.read_text(encoding="utf-8")
    assert text.startswith("---\nname: ncu-fb-capture\n")
    for rule in (
        "不按讚",
        "不留言",
        "不分享",
        "發文者",
        "data/raw/facebook/",
        "26 小時",
        "不讀取任何連結",  # ADR-0011：不懸停、不點擊、不讀 href／pathname
        "不懸停",
        "刪除內文中的聯絡方式",  # 電話、email、LINE ID、學號；姓名保留（使用者 2026-10-18 決定）
    ):
        assert rule in text


# --- 排程範本與安裝腳本 ---------------------------------------------------------------------


def test_plist_template_runs_daily_at_8_with_sonnet_and_chrome_only() -> None:
    raw = PLIST.read_text(encoding="utf-8")
    plist = plistlib.loads(
        raw.replace("__REPO__", "/Users/demo/ncu-resilience-ai")
        .replace("__CLAUDE__", "/Users/demo/.local/bin/claude")
        .encode()
    )
    assert plist["Label"] == "tw.ncu-resilience.fb-capture"
    assert plist["StartCalendarInterval"] == {"Hour": 8, "Minute": 0}
    command = " ".join(plist["ProgramArguments"])
    for part in (
        "/Users/demo/.local/bin/claude -p",
        "--chrome",
        "--model sonnet",
        "mcp__claude-in-chrome",
        "WebFetch",
        "WebSearch",
        "Bash",
        "/ncu-fb-capture",
    ):
        assert part in command
    assert "--dangerously-skip-permissions" not in command
    assert plist["WorkingDirectory"] == "/Users/demo/ncu-resilience-ai"
    assert plist["StandardOutPath"].startswith(
        "/Users/demo/ncu-resilience-ai/data/raw/facebook/logs/"
    )


def test_install_script_supports_install_uninstall_status() -> None:
    script = INSTALL.read_text(encoding="utf-8")
    assert script.startswith("#!/usr/bin/env bash\n")
    for word in ("install)", "uninstall)", "status)", "launchctl bootstrap", "launchctl bootout"):
        assert word in script
