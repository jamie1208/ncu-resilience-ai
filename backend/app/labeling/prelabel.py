"""規則式預標（P1-T09，ADR-0009 決定 4）。

匯出待標記文章時預先填入建議值，減少人工輸入；**只是建議，金標準一律由使用者確認**。
AI 預標延到 S01–S03（P2-T08）完成後再補。

規則：
- 校方網站（`ncu-*`）與氣象署（`cwa-*`）：官方公告、已核實、與校園相關
  （規格：官方來源 → confirmed）。
- 社群與人工匯入（`facebook`、`dcard`）：未核實；陳述性質與相關性留給人工判斷。
- 類別：依關鍵字，優先序為火災 > 氣象 > 健康 > 治安 > 資安 > 交通 > 設施 > 教務；
  氣象署來源一律為氣象。沒有命中時不預填。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.domain.states import VerificationStatus
from app.labeling.models import Category, ClaimType

_OFFICIAL_PREFIXES = ("ncu-", "cwa-")
_SOCIAL_SOURCES = frozenset({"facebook", "dcard"})

_KEYWORDS: tuple[tuple[Category, re.Pattern[str]], ...] = (
    (Category.FIRE, re.compile(r"火警|火災|起火|冒煙|瓦斯外洩|爆炸")),
    (Category.WEATHER, re.compile(r"颱風|豪雨|大雨|特報|地震|高溫|寒流|淹水")),
    (Category.HEALTH, re.compile(r"食物中毒|腸胃炎|傳染|流感|登革熱|確診|化學品|毒性")),
    (Category.SECURITY, re.compile(r"失竊|竊盜|偷竊|被偷|詐騙|可疑人士|騷擾|偷拍")),
    (Category.INFOSEC, re.compile(r"釣魚|盜用|帳號被盜|資安|惡意程式|勒索")),
    (Category.TRAFFIC, re.compile(r"車禍|擦撞|道路封閉|封路|拖吊|接駁車|交通管制")),
    (Category.FACILITY, re.compile(r"停電|停水|施工|維護|斷線|中斷|故障|跳電|漏水")),
    (Category.ACADEMIC, re.compile(r"停課|補課|調課|期中考|期末考|考試")),
)


@dataclass(frozen=True)
class Prelabel:
    """預填的建議值；None 表示不預填，由人工判斷。"""

    is_campus_relevant: bool | None = None
    category: Category | None = None
    claim_type: ClaimType | None = None
    verification: VerificationStatus | None = None


def _category(source_code: str, text: str) -> Category | None:
    if source_code.startswith("cwa-"):
        return Category.WEATHER
    for category, pattern in _KEYWORDS:
        if pattern.search(text):
            return category
    return None


def prelabel(source_code: str, text: str) -> Prelabel:
    """依來源與內文產生預標建議。"""
    category = _category(source_code, text)
    if source_code.startswith(_OFFICIAL_PREFIXES):
        return Prelabel(
            is_campus_relevant=True,
            category=category,
            claim_type=ClaimType.OFFICIAL,
            verification=VerificationStatus.VERIFIED,
        )
    if source_code in _SOCIAL_SOURCES:
        return Prelabel(category=category, verification=VerificationStatus.UNVERIFIED)
    return Prelabel(category=category)
