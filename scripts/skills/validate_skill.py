#!/usr/bin/env python3
"""產品 Skill 靜態驗證（P2-T02；規則見 docs/architecture/skill-standard.md）。

檢查檔案、frontmatter、SKILL.md 章節、schemas、golden cases 與錄製回應的格式。
輸出是否符合 expected／must_not 屬於評測，由 P2-T05 的評測框架檢查。

用法：python3 scripts/skills/validate_skill.py skills/*       # 也可以傳 skills/ 或單一 Skill 資料夾
結束碼：0 = 全部通過；1 = 有 Skill 未通過；2 = 沒有找到任何 Skill。
需要：pyyaml、jsonschema。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
SCHEMA_VERSION_RE = re.compile(r"^[1-9]\d*$")
FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*(\n|$)", re.S)
CASE_ID_RE = re.compile(r"^(normal|boundary|adversarial)-\d{3}$")

MAX_NAME = 64
MAX_DESCRIPTION = 1024
MIN_CASES = 20
SECTIONS = ("何時使用", "輸入", "步驟", "證據優先序", "禁止事項", "Abstain／Fallback", "輸出範例")
CASE_TYPES = ("normal", "boundary", "adversarial")
SPLITS = ("dev", "test")
CASE_FIELDS = ("id", "type", "split", "input", "expected", "must_not")
RECORDED_FIELDS = ("case_id", "skill_version", "model", "output")
OUTPUT_COMMON = ("status", "evidence_ids", "reason", "abstain_reason")
EVIDENCE_REQUIRED = ("evidence_id", "source_code", "text")
FORBIDDEN_KEYWORDS = ("oneOf", "allOf", "not", "if", "then", "else")
MUST_NOT_OPS = {"eq": True, "in": True, "contains": True, "matches": False}  # op → 是否需要 path


def is_template(skill: Path) -> bool:
    """底線開頭的資料夾是模板（ADR-0013）。"""
    return skill.name.startswith("_")


def expected_name(skill: Path) -> str:
    """frontmatter 的 name 應有的值：模板去掉開頭底線。"""
    return skill.name[1:] if is_template(skill) else skill.name


# --- SKILL.md ------------------------------------------------------------------------------


def check_skill_md(skill: Path, errs: list[str]) -> dict[str, Any]:
    """檢查 frontmatter 與章節，回傳 metadata（解析失敗時為空）。"""
    md = skill / "SKILL.md"
    text = md.read_text(encoding="utf-8")
    m = FRONTMATTER_RE.match(text)
    if not m:
        errs.append("SKILL.md 缺少 YAML frontmatter（開頭的 --- 區塊）")
        return {}
    try:
        fm = yaml.safe_load(m.group(1))
    except yaml.YAMLError as exc:
        errs.append(f"SKILL.md frontmatter 不是合法的 YAML：{exc}")
        return {}
    if not isinstance(fm, dict):
        errs.append("SKILL.md frontmatter 必須是對照表")
        return {}

    name = fm.get("name")
    if not isinstance(name, str) or not name:
        errs.append("frontmatter 缺 name")
    else:
        if len(name) > MAX_NAME or not NAME_RE.match(name):
            errs.append(
                f"name「{name}」只能用小寫英數字與單一連字號，不能以連字號開頭或結尾，"
                f"長度 ≤ {MAX_NAME}"
            )
        if name != expected_name(skill):
            errs.append(
                f"name「{name}」與資料夾「{skill.name}」不一致（應為「{expected_name(skill)}」）"
            )

    desc = fm.get("description")
    if not isinstance(desc, str) or not desc.strip():
        errs.append("frontmatter 缺 description")
    elif len(desc) > MAX_DESCRIPTION:
        errs.append(f"description 有 {len(desc)} 字元，超過 {MAX_DESCRIPTION}")

    meta = fm.get("metadata")
    if not isinstance(meta, dict):
        errs.append("frontmatter 缺 metadata")
        meta = {}
    version = meta.get("version")
    if not isinstance(version, str) or not SEMVER_RE.match(version):
        errs.append(f"metadata.version 必須是加引號的 MAJOR.MINOR.PATCH 字串，目前是 {version!r}")
    sv = meta.get("schema_version")
    if not isinstance(sv, str) or not SCHEMA_VERSION_RE.match(sv):
        errs.append(f"metadata.schema_version 必須是加引號的正整數字串，目前是 {sv!r}")

    body = text[m.end() :]
    found = re.findall(r"^## (.+?)\s*$", body, re.M)
    missing = [s for s in SECTIONS if s not in found]
    if missing:
        errs.append("SKILL.md 缺少章節：" + "、".join(f"## {s}" for s in missing))
    else:
        order = [found.index(s) for s in SECTIONS]
        if order != sorted(order):
            errs.append("SKILL.md 章節順序應為：" + "、".join(SECTIONS))
    return meta


# --- schemas -------------------------------------------------------------------------------


def load_schema(path: Path, errs: list[str]) -> dict[str, Any] | None:
    """讀取並檢查 JSON Schema 2020-12 是否合法。"""
    rel = f"schemas/{path.name}"
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        errs.append(f"{rel} 不是合法的 JSON：{exc}")
        return None
    if not isinstance(schema, dict):
        errs.append(f"{rel} 的根必須是物件")
        return None
    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        errs.append(f"{rel} 的 $schema 必須是 https://json-schema.org/draft/2020-12/schema")
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        errs.append(f"{rel} 不是合法的 JSON Schema：{exc.message}")
        return None
    return schema


def has_type(node: dict[str, Any], name: str) -> bool:
    t = node.get("type")
    return t == name or (isinstance(t, list) and name in t)


def check_strict(node: Any, path: str, errs: list[str]) -> None:
    """輸出 schema 必須是 OpenAI Structured Outputs strict 子集（skill-standard §6）。"""
    if isinstance(node, list):
        for i, child in enumerate(node):
            check_strict(child, f"{path}/{i}", errs)
        return
    if not isinstance(node, dict):
        return
    for kw in FORBIDDEN_KEYWORDS:
        if kw in node:
            errs.append(f"output.schema.json {path}：不可使用 {kw}")
    if has_type(node, "object"):
        props = node.get("properties", {})
        if node.get("additionalProperties") is not False:
            errs.append(f"output.schema.json {path}：物件必須設 additionalProperties: false")
        if set(node.get("required", [])) != set(props):
            errs.append(f"output.schema.json {path}：required 必須列出全部屬性")
    for key, child in node.items():
        if key in ("enum", "const", "default", "examples"):
            continue
        if key == "properties" and isinstance(child, dict):
            for prop, sub in child.items():
                check_strict(sub, f"{path}/properties/{prop}", errs)
        else:
            check_strict(child, f"{path}/{key}", errs)


def check_output_schema(schema: dict[str, Any], errs: list[str]) -> None:
    check_strict(schema, "#", errs)
    if not has_type(schema, "object"):
        errs.append("output.schema.json 的根必須是 object")
        return
    props = schema.get("properties", {})
    for field in OUTPUT_COMMON:
        if field not in props:
            errs.append(f"output.schema.json 缺共同欄位 {field}（skill-standard §6）")
    status = props.get("status", {})
    if isinstance(status, dict) and set(status.get("enum", [])) != {"ok", "abstain"}:
        errs.append('output.schema.json 的 status 必須是 enum ["ok", "abstain"]（允許 abstain）')


def check_input_schema(schema: dict[str, Any], errs: list[str]) -> None:
    props = schema.get("properties", {})
    evidence = props.get("evidence")
    if not isinstance(evidence, dict) or "evidence" not in schema.get("required", []):
        errs.append("input.schema.json 的根必須有必填的 evidence 陣列（skill-standard §5）")
        return
    items = evidence.get("items", {})
    required = items.get("required", []) if isinstance(items, dict) else []
    missing = [f for f in EVIDENCE_REQUIRED if f not in required]
    if missing:
        errs.append("input.schema.json 的 evidence 項目必須要求：" + "、".join(missing))


# --- golden cases --------------------------------------------------------------------------


def check_must_not(cid: str, rules: Any, errs: list[str]) -> None:
    if not isinstance(rules, list) or not rules:
        errs.append(f"{cid}：must_not 必須是至少 1 條規則的陣列")
        return
    for i, rule in enumerate(rules):
        where = f"{cid}：must_not[{i}]"
        if not isinstance(rule, dict):
            errs.append(f"{where} 必須是物件")
            continue
        op = rule.get("op")
        if op not in MUST_NOT_OPS:
            errs.append(f"{where} 的 op {op!r} 不支援（{'、'.join(MUST_NOT_OPS)}）")
            continue
        if "value" not in rule:
            errs.append(f"{where} 缺 value")
        if MUST_NOT_OPS[op] and not (isinstance(rule.get("path"), str) and rule["path"]):
            errs.append(f"{where}（{op}）缺 path")
        if op == "in" and not isinstance(rule.get("value"), list):
            errs.append(f"{where}（in）的 value 必須是陣列")
        if op == "matches":
            try:
                re.compile(str(rule.get("value", "")))
            except re.error as exc:
                errs.append(f"{where} 的正規表示式無效：{exc}")


def load_cases(skill: Path, errs: list[str]) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    lines = (skill / "tests" / "cases.jsonl").read_text(encoding="utf-8").splitlines()
    for no, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            case = json.loads(line)
        except json.JSONDecodeError as exc:
            errs.append(f"tests/cases.jsonl 第 {no} 行不是合法的 JSON：{exc}")
            continue
        if not isinstance(case, dict):
            errs.append(f"tests/cases.jsonl 第 {no} 行必須是物件")
            continue
        cases.append(case)
    return cases


def check_cases(
    skill: Path,
    cases: list[dict[str, Any]],
    input_schema: dict[str, Any] | None,
    output_schema: dict[str, Any] | None,
    errs: list[str],
) -> None:
    if len(cases) < MIN_CASES:
        errs.append(f"golden cases 只有 {len(cases)} 筆，至少需要 {MIN_CASES} 筆")
    types = {c.get("type") for c in cases}
    for t in CASE_TYPES:
        if t not in types:
            errs.append(f"golden cases 缺少 {t} 類案例")
    splits = {c.get("split") for c in cases}
    for s in SPLITS:
        if s not in splits:
            errs.append(f"golden cases 缺少 split = {s} 的案例")

    ids = [c.get("id") for c in cases]
    dup = sorted({str(i) for i in ids if ids.count(i) > 1})
    if dup:
        errs.append("case id 重複：" + "、".join(dup))

    validator = Draft202012Validator(input_schema) if input_schema else None
    out_props = set((output_schema or {}).get("properties", {}))
    for n, case in enumerate(cases, 1):
        cid = str(case.get("id", f"第 {n} 筆"))
        missing = [f for f in CASE_FIELDS if f not in case]
        if missing:
            errs.append(f"{cid}：缺欄位 " + "、".join(missing))
        m = CASE_ID_RE.match(cid)
        if not m:
            errs.append(f"{cid}：id 格式必須是 <type>-<三位數字>")
        elif case.get("type") in CASE_TYPES and m.group(1) != case["type"]:
            errs.append(f"{cid}：id 前綴與 type「{case['type']}」不一致")
        if "type" in case and case["type"] not in CASE_TYPES:
            errs.append(f"{cid}：type {case['type']!r} 不支援")
        if "split" in case and case["split"] not in SPLITS:
            errs.append(f"{cid}：split {case['split']!r} 不支援（dev／test）")
        if validator is not None and "input" in case:
            for e in validator.iter_errors(case["input"]):
                loc = "/".join(str(p) for p in e.absolute_path) or "(根)"
                errs.append(f"{cid}：input 不符合 input.schema.json（{loc}：{e.message}）")
        if "expected" in case:
            exp = case["expected"]
            if not isinstance(exp, dict) or not exp:
                errs.append(f"{cid}：expected 必須是非空物件")
            elif out_props:
                unknown = sorted(set(exp) - out_props)
                if unknown:
                    errs.append(f"{cid}：expected 有輸出 schema 沒有的欄位 " + "、".join(unknown))
        if "must_not" in case:
            check_must_not(cid, case["must_not"], errs)


def check_recorded(
    skill: Path,
    cases: list[dict[str, Any]],
    version: Any,
    output_schema: dict[str, Any] | None,
    errs: list[str],
) -> None:
    rec_dir = skill / "tests" / "recorded"
    validator = Draft202012Validator(output_schema) if output_schema else None
    ids = {str(c["id"]) for c in cases if "id" in c}
    for cid in sorted(ids):
        path = rec_dir / f"{cid}.json"
        if not path.is_file():
            errs.append(f"{cid}：缺錄製回應 tests/recorded/{cid}.json")
            continue
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errs.append(f"tests/recorded/{cid}.json 不是合法的 JSON：{exc}")
            continue
        if not isinstance(rec, dict):
            errs.append(f"tests/recorded/{cid}.json 必須是物件")
            continue
        missing = [f for f in RECORDED_FIELDS if f not in rec]
        if missing:
            errs.append(f"tests/recorded/{cid}.json 缺欄位 " + "、".join(missing))
        if "case_id" in rec and rec["case_id"] != cid:
            errs.append(f"tests/recorded/{cid}.json 的 case_id 是 {rec['case_id']!r}")
        if "skill_version" in rec and rec["skill_version"] != version:
            errs.append(
                f"tests/recorded/{cid}.json 的 skill_version {rec['skill_version']!r} "
                f"與 metadata.version {version!r} 不同，升版後要重新錄製"
            )
        if "model" in rec and not (isinstance(rec["model"], str) and rec["model"]):
            errs.append(f"tests/recorded/{cid}.json 的 model 不可為空")
        if validator is not None and "output" in rec:
            for e in validator.iter_errors(rec["output"]):
                loc = "/".join(str(p) for p in e.absolute_path) or "(根)"
                errs.append(f"{cid}：錄製輸出不符合 output.schema.json（{loc}：{e.message}）")
    for path in sorted(rec_dir.glob("*.json")):
        if path.stem not in ids:
            errs.append(f"tests/recorded/{path.name} 沒有對應的 case（{path.stem}）")


# --- 主流程 --------------------------------------------------------------------------------


REQUIRED_PATHS = (
    "SKILL.md",
    "schemas/input.schema.json",
    "schemas/output.schema.json",
    "tests/cases.jsonl",
)


def validate_skill(skill: Path) -> list[str]:
    """驗證一個 Skill 資料夾，回傳錯誤訊息（空陣列表示通過）。"""
    errs: list[str] = []
    for rel in REQUIRED_PATHS:
        if not (skill / rel).is_file():
            errs.append(f"缺 {rel}")
    if not (skill / "tests" / "recorded").is_dir():
        errs.append("缺 tests/recorded/")
    if not (skill / "references").is_dir() or not list((skill / "references").glob("*.md")):
        errs.append("references/ 至少要有 1 個 .md 檔")

    meta = check_skill_md(skill, errs) if (skill / "SKILL.md").is_file() else {}

    input_schema = output_schema = None
    if (skill / "schemas" / "input.schema.json").is_file():
        input_schema = load_schema(skill / "schemas" / "input.schema.json", errs)
        if input_schema is not None:
            check_input_schema(input_schema, errs)
    if (skill / "schemas" / "output.schema.json").is_file():
        output_schema = load_schema(skill / "schemas" / "output.schema.json", errs)
        if output_schema is not None:
            check_output_schema(output_schema, errs)

    if (skill / "tests" / "cases.jsonl").is_file():
        cases = load_cases(skill, errs)
        check_cases(skill, cases, input_schema, output_schema, errs)
        if (skill / "tests" / "recorded").is_dir():
            check_recorded(skill, cases, meta.get("version"), output_schema, errs)
    return errs


def collect(paths: list[Path]) -> list[Path]:
    """展開參數：Skill 資料夾直接收；含 Skill 的上層資料夾展開一層；檔案略過。"""
    skills: list[Path] = []
    for p in paths:
        if not p.is_dir():
            continue
        if (p / "SKILL.md").exists() or not any(c.is_dir() for c in p.iterdir()):
            skills.append(p)
        else:
            children = (c for c in p.iterdir() if c.is_dir() and not c.name.startswith("."))
            skills.extend(sorted(children))
    return skills


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="產品 Skill 靜態驗證（skill-standard.md）")
    parser.add_argument("paths", nargs="+", type=Path, help="Skill 資料夾、或 skills/ 本身")
    args = parser.parse_args(argv)

    skills = collect(args.paths)
    if not skills:
        print("沒有找到任何 Skill 資料夾")
        return 2
    failed = 0
    for skill in skills:
        errs = validate_skill(skill)
        if errs:
            failed += 1
            print(f"✗ {skill.name}（{len(errs)} 項）")
            for e in errs:
                print(f"  - {e}")
        else:
            print(f"✓ {skill.name}")
    print(f"共 {len(skills)} 個 Skill，{len(skills) - failed} 個通過，{failed} 個未通過")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
