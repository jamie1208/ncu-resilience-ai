"""OpenAI Provider（P2-T03）：以 httpx2 直接呼叫 Responses API 的 Structured Outputs（strict）。

- 請求帶 `store: false`，不在 OpenAI 端保存回應。
- 不在這裡重試；錯誤依 `retryable` 分類，由 Skill Runner 決定是否重送（P2-T04）。
- 錯誤訊息只保留 OpenAI 回傳的錯誤說明（截斷），並移除金鑰；不包含請求內容。
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from typing import Any

import httpx2
from pydantic import SecretStr

from app.ai.llm.base import (
    LLMAuthError,
    LLMError,
    LLMIncompleteError,
    LLMRefusalError,
    LLMRequestError,
    LLMResult,
    LLMUnavailableError,
    Message,
    Usage,
)

DEFAULT_BASE_URL = "https://api.openai.com/v1"
MAX_ERROR_CHARS = 300
RETRYABLE_STATUS = frozenset({408, 409, 429})
"""5xx 以外可重試的狀態碼；429 的 insufficient_quota（額度用完）例外，不可重試。"""


class OpenAIProvider:
    """OpenAI Responses API。`client` 可注入（測試用 MockTransport）。"""

    name = "openai"

    def __init__(
        self,
        api_key: SecretStr,
        *,
        base_url: str = DEFAULT_BASE_URL,
        reasoning_effort: str | None = None,
        client: httpx2.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._url = base_url.rstrip("/") + "/responses"
        self._reasoning_effort = reasoning_effort
        self._client = client

    def __repr__(self) -> str:
        return f"OpenAIProvider(url={self._url!r}, reasoning_effort={self._reasoning_effort!r})"

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
        """送出 Responses API 請求，回傳模型輸出的 JSON 文字；失敗時拋出 `LLMError`。"""
        body: dict[str, Any] = {
            "model": model,
            "input": [{"role": m.role, "content": m.content} for m in messages],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "schema": dict(json_schema),
                    "strict": True,
                }
            },
            "max_output_tokens": max_output_tokens,
            "store": False,
        }
        if self._reasoning_effort is not None:
            body["reasoning"] = {"effort": self._reasoning_effort}
        headers = {"Authorization": f"Bearer {self._api_key.get_secret_value()}"}

        started = time.monotonic()
        try:
            if self._client is not None:
                response = await self._client.post(
                    self._url, json=body, headers=headers, timeout=timeout
                )
            else:
                async with httpx2.AsyncClient() as client:
                    response = await client.post(
                        self._url, json=body, headers=headers, timeout=timeout
                    )
        except httpx2.TimeoutException as exc:
            raise LLMUnavailableError(f"OpenAI 逾時（{type(exc).__name__}）") from None
        except httpx2.TransportError as exc:
            raise LLMUnavailableError(f"OpenAI 連線失敗（{type(exc).__name__}）") from None
        latency_ms = int((time.monotonic() - started) * 1000)

        if response.status_code >= 400:
            raise self._http_error(response)
        try:
            data = response.json()
        except ValueError:
            raise LLMUnavailableError(
                "OpenAI 回應不是 JSON", status_code=response.status_code
            ) from None
        if not isinstance(data, dict):
            raise LLMUnavailableError("OpenAI 回應格式異常", status_code=response.status_code)
        return self._parse(data, requested_model=model, latency_ms=latency_ms)

    # --- 內部 ------------------------------------------------------------------------------

    def _scrub(self, text: str) -> str:
        """移除金鑰並截斷，避免錯誤訊息洩漏金鑰或過長。"""
        secret = self._api_key.get_secret_value()
        if secret:
            text = text.replace(secret, "***")
        return text[:MAX_ERROR_CHARS]

    def _http_error(self, response: httpx2.Response) -> LLMError:
        status = response.status_code
        code: str | None = None
        detail = ""
        try:
            err = response.json().get("error") or {}
            code = err.get("code")
            detail = str(err.get("message") or "")
        except (ValueError, AttributeError):
            detail = ""
        message = self._scrub(f"OpenAI HTTP {status}（{code or '-'}）：{detail}")
        retry_after = _retry_after(response)
        if status in (401, 403):
            return LLMAuthError(message, status_code=status)
        if status == 429 and code == "insufficient_quota":
            return LLMRequestError(message, status_code=status)
        if status >= 500 or status in RETRYABLE_STATUS:
            return LLMUnavailableError(message, status_code=status, retry_after=retry_after)
        return LLMRequestError(message, status_code=status)

    def _parse(self, data: dict[str, Any], *, requested_model: str, latency_ms: int) -> LLMResult:
        usage = _usage(data.get("usage"))
        status = data.get("status")
        if status == "incomplete":
            reason = (data.get("incomplete_details") or {}).get("reason") or "unknown"
            raise LLMIncompleteError(f"OpenAI 輸出未完成：{reason}", usage=usage)
        if status != "completed":
            raise LLMUnavailableError(f"OpenAI 回應狀態為 {status!r}", usage=usage)

        texts: list[str] = []
        for item in data.get("output") or []:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            for part in item.get("content") or []:
                kind = part.get("type") if isinstance(part, dict) else None
                if kind == "refusal":
                    refusal = self._scrub(str(part.get("refusal") or ""))
                    raise LLMRefusalError(f"模型拒絕回答：{refusal}", usage=usage)
                if kind == "output_text":
                    texts.append(str(part.get("text") or ""))
        if not texts:
            raise LLMUnavailableError("OpenAI 回應中沒有輸出文字", usage=usage)
        return LLMResult(
            provider=self.name,
            model=str(data.get("model") or requested_model),
            text="".join(texts),
            usage=usage or Usage(input_tokens=0, output_tokens=0),
            response_id=data.get("id"),
            latency_ms=latency_ms,
        )


def _usage(raw: Any) -> Usage | None:
    if not isinstance(raw, dict):
        return None
    return Usage(
        input_tokens=int(raw.get("input_tokens") or 0),
        output_tokens=int(raw.get("output_tokens") or 0),
        cached_input_tokens=int((raw.get("input_tokens_details") or {}).get("cached_tokens") or 0),
        reasoning_tokens=int((raw.get("output_tokens_details") or {}).get("reasoning_tokens") or 0),
    )


def _retry_after(response: httpx2.Response) -> float | None:
    value = response.headers.get("retry-after")
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None
