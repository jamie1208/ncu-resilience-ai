"""LLM Adapter 介面（P2-T03）。

業務邏輯只透過 `LLMProvider.generate_structured` 呼叫模型，
更換供應商或模型只需要改設定（閘門 G2-3）。
Provider 只負責送出請求與分類錯誤：回傳模型輸出的原始文字，JSON 解析、schema 與業務驗證、
重試與 abstain 由 Skill Runner 處理（P2-T04）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

Role = Literal["system", "user", "assistant"]


@dataclass(frozen=True)
class Message:
    """一則對話訊息。來源內容由 Runner 包在 `<untrusted_source>` 中後放進 user 訊息。"""

    role: Role
    content: str


@dataclass(frozen=True)
class Usage:
    """一次呼叫的 token 用量。`output_tokens` 已包含推理 token。"""

    input_tokens: int
    output_tokens: int
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0


@dataclass(frozen=True)
class LLMResult:
    """一次成功呼叫的結果。`text` 是模型輸出的原始文字（應為 JSON，但尚未驗證）。"""

    provider: str
    model: str
    text: str
    usage: Usage
    response_id: str | None
    latency_ms: int


class LLMError(Exception):
    """LLM 呼叫失敗。`retryable` 表示同一請求稍後重送可能成功。訊息不得包含金鑰或來源原文。"""

    retryable: bool = False

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
        usage: Usage | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after
        self.usage = usage


class LLMUnavailableError(LLMError):
    """暫時性失敗：逾時、連線失敗、5xx、408、429 限流、回應格式異常。"""

    retryable = True


class LLMAuthError(LLMError):
    """金鑰無效或沒有權限（401、403）。"""


class LLMRequestError(LLMError):
    """請求本身有誤或無法再送：其他 4xx、schema 不被接受、模型不存在、額度用完。"""


class LLMRefusalError(LLMError):
    """模型拒絕回答（Structured Outputs 的 refusal）。"""


class LLMIncompleteError(LLMError):
    """輸出未完成（例如達到 max_output_tokens）。`usage` 記錄已花費的 token。"""


@runtime_checkable
class LLMProvider(Protocol):
    """模型供應商。實作：`OpenAIProvider`（正式）、`FakeProvider`（測試與 CI）。"""

    name: str

    async def generate_structured(
        self,
        messages: Sequence[Message],
        json_schema: Mapping[str, Any],
        *,
        schema_name: str,
        model: str,
        timeout: float,
        max_output_tokens: int,
    ) -> LLMResult:
        """送出訊息，要求輸出符合 `json_schema`（strict）的 JSON；失敗時拋出 `LLMError`。"""
        ...
