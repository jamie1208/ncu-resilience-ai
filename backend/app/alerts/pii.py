"""通知內容的 PII 遮蔽（P3-T07）。

通知內容在存入 alerts.body 前一律經過 `mask_pii()`：Email、手機與市話、身分證字號、學號，沿用
`app.ai.runner.validate.PII_PATTERNS`，與模型輸出的 PII 檢查一致。使用者 2026-10-22 決定只沿用
既有規則，不辨識人名或「姓氏＋稱謂」；未核實事件的通知不帶標題與原文，人名不會出現在那裡。
"""

from __future__ import annotations

from app.ai.runner.validate import PII_PATTERNS

MASK = "［已遮蔽］"


def mask_pii(text: str) -> str:
    """回傳遮蔽個資後的文字；不含個資的文字原樣回傳。"""
    for _kind, pattern in PII_PATTERNS:
        text = pattern.sub(MASK, text)
    return text
