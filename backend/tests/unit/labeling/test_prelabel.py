"""P1-T09：規則式預標（ADR-0009 決定 4）。預標只是建議，金標準由人工確認。"""

from __future__ import annotations

import pytest

from app.domain.states import VerificationStatus
from app.labeling.models import Category, ClaimType
from app.labeling.prelabel import Prelabel, prelabel


def test_official_campus_source_is_official_and_verified() -> None:
    hint = prelabel("ncu-oga-news", "工程五館停電通知\n\n10/20 13:00–17:00 停電")
    assert hint.is_campus_relevant is True
    assert hint.claim_type is ClaimType.OFFICIAL
    assert hint.verification is VerificationStatus.VERIFIED
    assert hint.category is Category.FACILITY


def test_cwa_alerts_are_weather_and_verified() -> None:
    hint = prelabel("cwa-alerts", "中央氣象署發布桃園市大雨特報。")
    assert hint.category is Category.WEATHER
    assert hint.claim_type is ClaimType.OFFICIAL
    assert hint.verification is VerificationStatus.VERIFIED


def test_social_source_is_unverified_without_claim_type() -> None:
    hint = prelabel("facebook", "後門機車格今天被拖吊了好幾台")
    assert hint.verification is VerificationStatus.UNVERIFIED
    assert hint.claim_type is None
    assert hint.is_campus_relevant is None
    assert hint.category is Category.TRAFFIC


@pytest.mark.parametrize(
    ("text", "category"),
    [
        ("圖書館三樓疑似火警，請勿靠近", Category.FIRE),
        ("颱風假：明日停止上班上課", Category.WEATHER),
        ("宿舍腳踏車失竊，請大家上鎖", Category.SECURITY),
        ("學生餐廳疑似食物中毒", Category.HEALTH),
        ("請注意假冒計中的釣魚信", Category.INFOSEC),
        ("中大路施工，道路封閉", Category.TRAFFIC),
        ("電算中心網路維護，服務暫停", Category.FACILITY),
        ("微積分期中考延期一週", Category.ACADEMIC),
    ],
)
def test_keyword_categories(text: str, category: Category) -> None:
    assert prelabel("ncu-announcements", text).category is category


def test_fire_takes_priority_over_facility() -> None:
    assert prelabel("ncu-announcements", "實驗室火警導致停電").category is Category.FIRE


def test_no_keyword_leaves_category_empty() -> None:
    assert prelabel("ncu-announcements", "歡迎參加校慶音樂會").category is None


def test_unknown_source_has_no_source_hints() -> None:
    assert prelabel("someone-else", "歡迎參加校慶音樂會") == Prelabel()
