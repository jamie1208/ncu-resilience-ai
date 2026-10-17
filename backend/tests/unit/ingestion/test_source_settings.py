"""P1-T03：來源連線設定檔 config/sources.yaml 與載入器。"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

from app.ingestion.registry import registry
from app.ingestion.source_settings import (
    DEFAULT_SOURCES_FILE,
    build_config,
    load_source_settings,
)
from app.ingestion.types import AccessPolicy

importlib.import_module("app.ingestion.adapters")  # 註冊全部 Adapter 類型

REPO = Path(__file__).resolve().parents[4]
INVENTORY = REPO / "docs" / "governance" / "source-inventory.md"


def approved_in_inventory() -> set[str]:
    """盤點總表中「核准狀態」為「核准」的來源代碼（與 check_source_inventory.py 獨立實作）。"""
    section = INVENTORY.read_text(encoding="utf-8").split("## 總表", 1)[1].split("\n## ", 1)[0]
    codes = set()
    for line in section.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if re.fullmatch(r"[a-z0-9][a-z0-9-]*", cells[0]) and cells[-1] == "核准":
            codes.add(cells[0])
    return codes


def write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "sources.yaml"
    p.write_text(text, encoding="utf-8")
    return p


# --- repo 內的設定檔 -------------------------------------------------------------------


def test_default_file_is_repo_config() -> None:
    assert DEFAULT_SOURCES_FILE == REPO / "config" / "sources.yaml"
    assert DEFAULT_SOURCES_FILE.is_file()


def test_repo_sources_are_all_approved_in_inventory() -> None:
    settings = load_source_settings()
    assert settings, "config/sources.yaml 至少要登記一個來源"
    not_approved = set(settings) - approved_in_inventory()
    assert not not_approved, f"未在來源盤點核准：{sorted(not_approved)}"


def test_repo_sources_have_registered_types_and_valid_options() -> None:
    for code, s in load_source_settings().items():
        assert s.type in registry.types(), code
        # 以核准、啟用的狀態建立 Adapter，確認 options 通過各 Adapter 的驗證
        adapter = registry.create(build_config(code, s, access_policy="approved", enabled=True))
        assert adapter.code == code


def test_repo_registers_rpage_sources() -> None:
    rpage = {c for c, s in load_source_settings().items() if s.type == "rpage"}
    assert rpage == {"ncu-announcements", "ncu-oia", "ncu-sec", "ncu-aa"}


# --- 載入器 ----------------------------------------------------------------------------


def test_load_and_build_config(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        "sources:\n"
        "  demo-feed:\n"
        "    type: rss\n"
        "    options:\n"
        "      url: https://news.example.edu/rss.xml\n",
    )
    settings = load_source_settings(path)
    assert list(settings) == ["demo-feed"]
    cfg = build_config("demo-feed", settings["demo-feed"], access_policy="pending", enabled=True)
    assert cfg.code == "demo-feed"
    assert cfg.type == "rss"
    assert cfg.access_policy is AccessPolicy.PENDING
    assert cfg.options["url"] == "https://news.example.edu/rss.xml"


def test_options_default_to_empty(tmp_path: Path) -> None:
    settings = load_source_settings(write(tmp_path, "sources:\n  demo:\n    type: manual\n"))
    assert dict(settings["demo"].options) == {}


@pytest.mark.parametrize(
    "text",
    [
        "",  # 空檔
        "- a\n- b\n",  # 不是 mapping
        "sources: []\n",
        "sources:\n  demo:\n    options: {}\n",  # 缺 type
        "sources:\n  demo:\n    type: rss\n    url: https://x.example\n",  # 未定義欄位
        "sources:\n  Demo_Feed:\n    type: rss\n",  # 代碼格式
        "sources:\n  demo:\n    type: rss\n  demo:\n    type: rpage\n",  # 重複代碼
        "other: 1\nsources:\n  demo:\n    type: rss\n",  # 未定義的頂層欄位
    ],
)
def test_invalid_files_are_rejected(tmp_path: Path, text: str) -> None:
    with pytest.raises(ValueError):
        load_source_settings(write(tmp_path, text))


def test_unsafe_yaml_tags_are_rejected(tmp_path: Path) -> None:
    text = "sources: !!python/object/apply:os.system ['echo pwned']\n"
    with pytest.raises(ValueError):
        load_source_settings(write(tmp_path, text))


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_source_settings(tmp_path / "nope.yaml")
