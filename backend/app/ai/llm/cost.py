"""Token 與費用記帳（P2-T03）。

- `call_cost`：依實際用量與價格表計算費用。
- `guard_call`：送出前以最壞情況估算，超過每次上限就不送；
  輸入以 UTF-8 位元組數當 token 上限，輸出以 max_output_tokens 計算。
- `CostLedger`：每日累計。行程內記帳；P2-T04 會從 ai_analysis_runs 讀取當日已花費作為起點。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, NonNegativeFloat

from app.ai.llm.base import LLMError, Message, Usage

PER_MILLION = 1_000_000
MESSAGE_OVERHEAD_TOKENS = 16
"""每則訊息的格式開銷（角色、分隔符號），估算時加上。"""


class ModelPrice(BaseModel):
    """每百萬 token 的價格（USD）。`cached_input` 為 None 表示沒有快取折扣。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    input: NonNegativeFloat
    cached_input: NonNegativeFloat | None = None
    output: NonNegativeFloat


class CostLimitExceededError(LLMError):
    """超過費用上限，請求沒有送出。"""


def call_cost(usage: Usage, price: ModelPrice) -> float:
    """一次呼叫的費用（USD）。快取的輸入 token 以 `cached_input` 計價。"""
    cached = (
        min(usage.cached_input_tokens, usage.input_tokens) if price.cached_input is not None else 0
    )
    cached_rate = price.cached_input or 0.0
    total = (
        (usage.input_tokens - cached) * price.input
        + cached * cached_rate
        + usage.output_tokens * price.output
    )
    return total / PER_MILLION


def estimate_input_tokens(messages: Sequence[Message], json_schema: Mapping[str, Any]) -> int:
    """輸入 token 的上限估計：每個 token 至少 1 個位元組，所以位元組數一定不少於 token 數。"""
    size = sum(len(m.content.encode("utf-8")) + MESSAGE_OVERHEAD_TOKENS for m in messages)
    size += len(json.dumps(json_schema, ensure_ascii=False).encode("utf-8"))
    return size


def guard_call(
    messages: Sequence[Message],
    json_schema: Mapping[str, Any],
    price: ModelPrice,
    *,
    max_output_tokens: int,
    per_call_usd: float,
) -> float:
    """回傳這次呼叫的最壞情況費用；超過 `per_call_usd` 時拋出 `CostLimitExceededError`。"""
    worst = Usage(
        input_tokens=estimate_input_tokens(messages, json_schema),
        output_tokens=max_output_tokens,
    )
    estimate = call_cost(worst, price)
    if estimate > per_call_usd:
        raise CostLimitExceededError(
            f"預估費用 US${estimate:.4f} 超過每次上限 US${per_call_usd:.4f}，未送出"
        )
    return estimate


@dataclass
class CostLedger:
    """每日費用累計。`check` 在送出前檢查，`record` 在呼叫後記錄實際費用。"""

    daily_usd: float
    spent_usd: float = 0.0

    def check(self, estimate_usd: float) -> None:
        """加上這次的預估費用會超過每日上限時，拋出 `CostLimitExceededError`。"""
        if self.spent_usd + estimate_usd > self.daily_usd:
            raise CostLimitExceededError(
                f"今日已花費 US${self.spent_usd:.4f}，加上預估 US${estimate_usd:.4f} "
                f"會超過每日上限 US${self.daily_usd:.2f}，未送出"
            )

    def record(self, cost_usd: float) -> None:
        """記錄一次呼叫的實際費用。"""
        self.spent_usd += cost_usd
