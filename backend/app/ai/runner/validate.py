"""驗證模型輸出（P2-T04）：JSON、schema、業務規則、evidence_id 存在、個資。

問題（`Issue`）只記錄代碼與位置，不含輸出內容，所以可以安全地寫進 ai_analysis_runs 與日誌。
個資規則（使用者 2026-10-19 決定）：電話、email、學號（9 位數字）、身分證號；不檢查人名。
"""

from __future__ import annotations

import json
import re
from collections.abc import Collection, Iterator
from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator

from app.ai.runner.loader import Skill


@dataclass(frozen=True)
class Issue:
    """一個驗證問題。`code`：json、schema、business、evidence、pii；`detail` 不含輸出內容。"""

    code: str
    detail: str


@dataclass(frozen=True)
class PIIMatch:
    """一處疑似個資；只記錄種類，不記錄內容。"""

    kind: str


PII_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")),
    ("phone", re.compile(r"(?<!\d)09\d{2}[-\s]?\d{3}[-\s]?\d{3}(?!\d)")),
    ("phone", re.compile(r"(?<!\d)\(?0[2-8]\)?[-\s]?\d{3,4}[-\s]?\d{4}(?!\d)")),
    ("national_id", re.compile(r"(?<![A-Za-z0-9])[A-Z][12]\d{8}(?!\d)")),
    ("student_id", re.compile(r"(?<![\d.])\d{9}(?![\d.])")),
)


def find_pii(text: str) -> list[PIIMatch]:
    """回傳文字中疑似個資的種類（每種最多一次）。"""
    kinds: list[str] = []
    for kind, pattern in PII_PATTERNS:
        if kind not in kinds and pattern.search(text):
            kinds.append(kind)
    return [PIIMatch(k) for k in kinds]


def parse_output(text: str) -> tuple[dict[str, Any] | None, list[Issue]]:
    """解析模型輸出的 JSON 文字；根必須是物件。"""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, [Issue("json", f"不是合法的 JSON（第 {exc.lineno} 行第 {exc.colno} 欄）")]
    if not isinstance(data, dict):
        return None, [Issue("json", "根必須是物件")]
    return data, []


def _strings(node: Any, path: str = "") -> Iterator[tuple[str, str]]:
    if isinstance(node, str):
        yield path or "/", node
    elif isinstance(node, dict):
        for key, value in node.items():
            if key == "evidence_ids":
                continue
            yield from _strings(value, f"{path}/{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _strings(value, f"{path}/{i}")


def validate_output(
    output: dict[str, Any], skill: Skill, evidence_ids: Collection[str]
) -> list[Issue]:
    """檢查已解析的輸出；回傳問題清單（空清單表示可以使用）。"""
    schema_issues = [
        Issue("schema", f"/{'/'.join(str(p) for p in e.absolute_path)}：{e.validator}")
        for e in Draft202012Validator(skill.output_schema).iter_errors(output)
    ]
    if schema_issues:
        return schema_issues

    issues: list[Issue] = []
    status = output.get("status")
    cited = output.get("evidence_ids") or []
    if status == "ok":
        if not cited:
            issues.append(Issue("business", "status 為 ok 時 evidence_ids 至少要有 1 個"))
        if output.get("abstain_reason") is not None:
            issues.append(Issue("business", "status 為 ok 時 abstain_reason 必須為 null"))
    elif status == "abstain" and output.get("abstain_reason") is None:
        issues.append(Issue("business", "status 為 abstain 時必須有 abstain_reason"))

    unknown = [e for e in cited if e not in set(evidence_ids)]
    if unknown:
        issues.append(Issue("evidence", f"引用了 {len(unknown)} 個不存在於輸入的 evidence_id"))

    for path, text in _strings(output):
        for match in find_pii(text):
            issues.append(Issue("pii", f"{path}：疑似{match.kind}"))
    return issues
