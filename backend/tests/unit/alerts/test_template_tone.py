"""P8-T10：通知範本的播報員語氣（ADR-0018 決策 3，docs/governance/tone-guide.md）。

1. 事實模板（已核實或官方來源）改成「【類別快報】」的播報開頭，欄位與順序不變；
2. 未查證的中性「一般安全提醒」屬「尚未查證」例外，維持平實、逐字不變；
3. 兩個模板都不使用表情符號。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.alerts.policy import TEMPLATE_DIR, Template

GENERAL_SAFETY = (
    "【$label｜一般安全提醒】有未經查證的訊息提到可能有$label相關狀況，目前無法確認是否屬實。\n"
    "地點：$location　日期：$date\n"
    "建議：$advice\n"
)
EMOJI = re.compile(r"(?![←-⇿])[\U0001f000-\U0001faff☀-➿️]")


def _read(template: Template) -> str:
    return (Path(TEMPLATE_DIR) / f"{template.value}.txt").read_text(encoding="utf-8")


def test_general_safety_template_stays_plain() -> None:
    assert _read(Template.GENERAL_SAFETY) == GENERAL_SAFETY


def test_factual_template_uses_broadcaster_opening() -> None:
    first = _read(Template.FACTUAL).splitlines()[0]
    assert first == "【$label快報】$title"


def test_factual_template_keeps_all_fields() -> None:
    raw = _read(Template.FACTUAL)
    for field in ("$label", "$title", "$location", "$date", "$basis", "$advice"):
        assert field in raw
    assert "未經查證" not in raw


@pytest.mark.parametrize("template", list(Template))
def test_templates_have_no_emoji(template: Template) -> None:
    assert not EMOJI.search(_read(template))
