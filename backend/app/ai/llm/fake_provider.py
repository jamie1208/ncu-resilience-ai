"""Fake Provider（P2-T03）：依序回傳預先準備的回應，CI 與測試不呼叫真實 API。

回應可以是：
- 物件：序列化為 JSON 文字回傳（例如錄製回應的 `output`）；
- 字串：原樣回傳（用來模擬無效 JSON）；
- `LLMError`：直接拋出（用來模擬逾時、拒答等）。

錄製回應格式見 docs/architecture/skill-standard.md §8（`tests/recorded/<case id>.json`）。
"""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.ai.llm.base import LLMError, LLMResult, Message, Usage

Scripted = Mapping[str, Any] | str | LLMError


@dataclass(frozen=True)
class FakeCall:
    """一次呼叫的參數，供測試檢查 Runner 送出的內容。"""

    messages: tuple[Message, ...]
    json_schema: Mapping[str, Any]
    schema_name: str
    model: str
    timeout: float
    max_output_tokens: int


class FakeProvider:
    """依序回傳 `responses`；用完後再呼叫會拋出 `LLMError`。"""

    name = "fake"

    def __init__(
        self,
        responses: Iterable[Scripted],
        *,
        model: str | None = None,
        usage: Usage | None = None,
    ) -> None:
        self._queue: deque[Scripted] = deque(responses)
        self._model = model
        self._usage = usage or Usage(input_tokens=0, output_tokens=0)
        self.calls: list[FakeCall] = []

    @classmethod
    def from_recorded(cls, *paths: Path) -> FakeProvider:
        """讀取錄製回應檔（skill-standard §8），依序回傳各檔的 `output`；模型名稱取自檔案。"""
        records = [json.loads(p.read_text(encoding="utf-8")) for p in paths]
        model = str(records[0]["model"]) if records else None
        return cls([r["output"] for r in records], model=model)

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
        """記錄呼叫參數，回傳下一個預先準備的回應。"""
        self.calls.append(
            FakeCall(
                messages=tuple(messages),
                json_schema=json_schema,
                schema_name=schema_name,
                model=model,
                timeout=timeout,
                max_output_tokens=max_output_tokens,
            )
        )
        if not self._queue:
            raise LLMError("FakeProvider 沒有更多錄製回應")
        item = self._queue.popleft()
        if isinstance(item, LLMError):
            raise item
        text = item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
        return LLMResult(
            provider=self.name,
            model=self._model or model,
            text=text,
            usage=self._usage,
            response_id=None,
            latency_ms=0,
        )
