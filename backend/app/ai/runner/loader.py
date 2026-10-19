"""載入產品 Skills（P2-T04）：掃描 `skills/`、解析 frontmatter、載入 schemas 與 references。

只載入 `skills/`（AGENTS.md 規則 4）；底線開頭的資料夾是模板，`discover_skills` 略過（ADR-0013），
但可以用 `load_skill` 明確載入（評測框架用模板示範）。完整的格式檢查由 validate_skill.py 負責。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

SKILLS_DIR = Path(__file__).resolve().parents[4] / "skills"
FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", re.S)


class SkillLoadError(ValueError):
    """Skill 資料夾缺檔或格式錯誤。"""


@dataclass(frozen=True)
class Reference:
    """`references/` 中的一份文件。"""

    name: str
    text: str


@dataclass(frozen=True)
class Skill:
    """已載入的 Skill。`instructions` 是 SKILL.md 去掉 frontmatter 的本文（系統指令）。"""

    name: str
    version: str
    schema_version: str
    description: str
    root: Path
    instructions: str
    references: tuple[Reference, ...]
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]


def _expected_name(path: Path) -> str:
    return path.name[1:] if path.name.startswith("_") else path.name


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise SkillLoadError(f"缺 {path.parent.name}/{path.name}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SkillLoadError(f"{path.name} 不是合法的 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise SkillLoadError(f"{path.name} 的根必須是物件")
    return data


def load_skill(path: Path) -> Skill:
    """載入一個 Skill 資料夾；缺檔、frontmatter 錯誤或名稱不符時拋出 `SkillLoadError`。"""
    md = path / "SKILL.md"
    if not md.is_file():
        raise SkillLoadError(f"{path.name}：缺 SKILL.md")
    text = md.read_text(encoding="utf-8")
    m = FRONTMATTER.match(text)
    if not m:
        raise SkillLoadError(f"{path.name}：SKILL.md 缺少 YAML frontmatter")
    try:
        fm = yaml.safe_load(m.group(1))
    except yaml.YAMLError as exc:
        raise SkillLoadError(f"{path.name}：frontmatter 不是合法的 YAML") from exc
    if not isinstance(fm, dict):
        raise SkillLoadError(f"{path.name}：frontmatter 必須是對照表")
    name = fm.get("name")
    if name != _expected_name(path):
        raise SkillLoadError(f"{path.name}：frontmatter name {name!r} 與資料夾不一致")
    meta = fm.get("metadata") or {}
    version, schema_version = meta.get("version"), meta.get("schema_version")
    if not isinstance(version, str) or not isinstance(schema_version, str):
        raise SkillLoadError(f"{path.name}：缺 metadata.version 或 metadata.schema_version")

    ref_dir = path / "references"
    references = tuple(
        Reference(p.name, p.read_text(encoding="utf-8"))
        for p in sorted(ref_dir.glob("*.md"))
        if ref_dir.is_dir()
    )
    return Skill(
        name=name,
        version=version,
        schema_version=schema_version,
        description=str(fm.get("description") or ""),
        root=path,
        instructions=text[m.end() :].strip() + "\n",
        references=references,
        input_schema=_read_json(path / "schemas" / "input.schema.json"),
        output_schema=_read_json(path / "schemas" / "output.schema.json"),
    )


def discover_skills(root: Path = SKILLS_DIR) -> dict[str, Skill]:
    """載入 `root` 底下的所有產品 Skill（略過底線或點開頭的資料夾與檔案），依名稱排序。"""
    skills: dict[str, Skill] = {}
    for path in sorted(root.iterdir()):
        if not path.is_dir() or path.name.startswith(("_", ".")):
            continue
        skill = load_skill(path)
        skills[skill.name] = skill
    return skills
