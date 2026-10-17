"""來源連線設定檔 `config/sources.yaml` 的載入器。

依 P1-T01 的決定：Adapter 的連線設定（類型與 options）放在 repo 的設定檔，以來源代碼對應；
授權狀態（access_policy）與啟用與否以資料庫 `sources` 表為準，由排程器（P1-T07）合併。
設定檔只能登記來源盤點中核准的來源（由測試檢查）。
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from app.ingestion.types import AccessPolicy, SourceConfig

DEFAULT_SOURCES_FILE = Path(__file__).resolve().parents[3] / "config" / "sources.yaml"
"""repo 根目錄的 config/sources.yaml。"""

SourceCode = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]*$")]


class SourceSettings(BaseModel):
    """單一來源的連線設定。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    options: dict[str, Any] = Field(default_factory=dict)


class _SourcesFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sources: dict[SourceCode, SourceSettings]


def _check_duplicate_keys(node: yaml.Node | None) -> None:
    """PyYAML 遇到重複的鍵會讓後者靜默覆蓋前者；在節點樹上先檢查。"""
    if isinstance(node, yaml.MappingNode):
        seen: set[str] = set()
        for key_node, value_node in node.value:
            key = str(key_node.value)
            if key in seen:
                raise ValueError(f"重複的鍵：{key}（第 {key_node.start_mark.line + 1} 行）")
            seen.add(key)
            _check_duplicate_keys(value_node)
    elif isinstance(node, yaml.SequenceNode):
        for child in node.value:
            _check_duplicate_keys(child)


def load_source_settings(path: Path = DEFAULT_SOURCES_FILE) -> dict[str, SourceSettings]:
    """讀取設定檔，回傳 {來源代碼: 設定}。

    格式錯誤時拋出 ValueError，檔案不存在時拋出 FileNotFoundError。
    """
    text = path.read_text(encoding="utf-8")
    try:
        _check_duplicate_keys(yaml.compose(text, Loader=yaml.SafeLoader))
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"{path.name} 不是有效的 YAML：{exc}") from None
    try:
        return dict(_SourcesFile.model_validate(data).sources)
    except ValidationError as exc:
        raise ValueError(f"{path.name} 格式錯誤：{exc}") from None


def build_config(
    code: str,
    settings: SourceSettings,
    *,
    access_policy: AccessPolicy | str,
    enabled: bool,
) -> SourceConfig:
    """合併設定檔與資料庫狀態，產生建立 Adapter 用的 `SourceConfig`。"""
    options: Mapping[str, Any] = settings.options
    return SourceConfig(
        code=code,
        type=settings.type,
        access_policy=AccessPolicy(access_policy),
        enabled=enabled,
        options=options,
    )
