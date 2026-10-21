"""通知內容的 PII 遮蔽（P3-T07）。

通知內容在存入 alerts.body 前一律經過 `mask_pii()`：
- Email、手機與市話、身分證字號、學號：沿用 `app.ai.runner.validate.PII_PATTERNS`，與模型輸出的
  PII 檢查一致；
- 「姓氏＋名字＋稱謂」（例：王小明同學、林老師）：換成「某＋稱謂」。只認常見姓氏，
  沒有姓氏的稱謂（例：資工系同學）保留原樣。
"""

from __future__ import annotations

import re

from app.ai.runner.validate import PII_PATTERNS

MASK = "［已遮蔽］"

_SURNAMES = (
    "陳林黃張李王吳劉蔡楊許鄭謝郭洪曾邱廖賴周徐蘇葉莊呂江何蕭羅高潘簡朱鍾彭游詹胡施沈余趙盧梁顏柯"
    "孫魏翁戴范宋方鄧杜傅侯曹薛丁卓馬阮董溫唐藍蔣石古紀姚連馮歐程湯黃田康姜汪白鄒尤巫鐘黎涂龔嚴韓袁"
    "金童陸夏柳邵錢伍倪溫于譚駱熊任甘秦顧毛章史官萬俞雷粘饒"
)
_HONORIFICS = (
    "同學",
    "老師",
    "教授",
    "助教",
    "教官",
    "主任",
    "校長",
    "學長",
    "學姐",
    "學姊",
    "學弟",
    "學妹",
    "先生",
    "小姐",
    "太太",
    "阿姨",
    "伯伯",
)
_NAMED_PERSON = re.compile(f"[{_SURNAMES}][一-鿿]{{0,2}}?({'|'.join(_HONORIFICS)})")


def mask_pii(text: str) -> str:
    """回傳遮蔽個資後的文字；不含個資的文字原樣回傳。"""
    for _kind, pattern in PII_PATTERNS:
        text = pattern.sub(MASK, text)
    return _NAMED_PERSON.sub(lambda m: f"某{m.group(1)}", text)
