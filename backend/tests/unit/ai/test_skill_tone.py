"""P8-T11：AI 產生內容改為播報員語氣（ADR-0018 決策 3，docs/governance/tone-guide.md）。

範圍（使用者 2026-11-10 決定）：只改會顯示給讀者的 S02 event-extractor 與 S12 series-analyst；
S07／S08 已停用（P8-T13），S11 沒有對外文字。

1. SKILL.md 寫明播報員語氣的規則與例外，並升版本；
2. 錄製回應全部以新版本重錄；
3. 播報欄位不用表情符號、不聳動、整段最多一個驚嘆號；
4. 推測（S12 的原因）仍帶「可能」「疑似」，語氣不讓推測聽起來像結論；
5. 新版本有真實模型（--live）的評測報告且達門檻。
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[4]
SKILLS = ROOT / "skills"
REPORTS = ROOT / "eval" / "reports"

# 名稱 → (升版後的版本, 播報語氣欄位的取值函式)
EMOJI = re.compile(r"(?![←-⇿])[\U0001f000-\U0001faff☀-➿️]|\(\^|\^\)|（\^")
SENSATIONAL = ("驚爆", "震撼", "速看", "快看", "超扯", "嚇死")


def _s02_texts(output: dict[str, Any]) -> Iterator[str]:
    summary = output.get("summary")
    if isinstance(summary, dict) and summary.get("text"):
        yield str(summary["text"])


def _s12_texts(output: dict[str, Any]) -> Iterator[str]:
    if output.get("reason"):
        yield str(output["reason"])
    for measure in output.get("measures") or []:
        yield str(measure.get("action") or "")


SKILLS_UNDER_TEST = {
    "event-extractor": ("1.2.0", _s02_texts),
    "series-analyst": ("1.1.0", _s12_texts),
}


def _version(name: str) -> str:
    text = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
    match = re.search(r'^\s*version:\s*"([^"]+)"', text, re.MULTILINE)
    assert match, f"{name} 的 SKILL.md 沒有 version"
    return match.group(1)


def _recorded(name: str) -> list[dict[str, Any]]:
    paths = sorted((SKILLS / name / "tests" / "recorded").glob("*.json"))
    assert paths, f"{name} 沒有錄製回應"
    return [json.loads(p.read_text(encoding="utf-8")) for p in paths]


@pytest.mark.parametrize("name", SKILLS_UNDER_TEST)
def test_skill_version_bumped(name: str) -> None:
    assert _version(name) == SKILLS_UNDER_TEST[name][0]


@pytest.mark.parametrize("name", SKILLS_UNDER_TEST)
def test_skill_md_has_broadcaster_rules(name: str) -> None:
    text = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
    assert "播報員" in text
    assert "tone-guide.md" in text
    assert "表情符號" in text
    # 例外：查證標籤與推測維持平實
    assert "尚未查證" in text


@pytest.mark.parametrize("name", SKILLS_UNDER_TEST)
def test_recorded_responses_rerecorded_with_new_version(name: str) -> None:
    version = SKILLS_UNDER_TEST[name][0]
    stale = [r["case_id"] for r in _recorded(name) if r.get("skill_version") != version]
    assert not stale, f"{name} 尚未以 {version} 重錄：{stale}"


@pytest.mark.parametrize("name", SKILLS_UNDER_TEST)
def test_broadcaster_fields_follow_tone_rules(name: str) -> None:
    extract = SKILLS_UNDER_TEST[name][1]
    for record in _recorded(name):
        for text in extract(record.get("output") or {}):
            assert not EMOJI.search(text), (record["case_id"], text)
            assert not any(word in text for word in SENSATIONAL), (record["case_id"], text)
            assert text.count("!") + text.count("！") <= 1, (record["case_id"], text)


def test_series_causes_stay_hedged() -> None:
    for record in _recorded("series-analyst"):
        for cause in (record.get("output") or {}).get("causes") or []:
            description = str(cause.get("description") or "")
            assert any(word in description for word in ("可能", "疑似")), (
                record["case_id"],
                description,
            )


@pytest.mark.parametrize("name", SKILLS_UNDER_TEST)
def test_live_report_for_new_version_passes(name: str) -> None:
    version = SKILLS_UNDER_TEST[name][0]
    path = REPORTS / name / f"{version}.json"
    assert path.exists(), f"缺少 {path.relative_to(ROOT)}"
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["mode"] == "live"
    assert report["split"] == "test"
    assert report["passed"] is True
