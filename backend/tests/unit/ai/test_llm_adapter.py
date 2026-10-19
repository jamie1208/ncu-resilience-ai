"""P2-T03：LLM Adapter（base、openai_provider、fake_provider、cost、config）。

- OpenAI 以 httpx2 直接呼叫 Responses API（Structured Outputs，strict JSON schema）；
  測試用 MockTransport，不連網。
- 模型名稱、價格與費用上限只出現在 `config/llm.yaml`。
- 使用者 2026-10-19 決定：gpt-5-mini、每次 US$0.05、每日 US$1。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx2
import pytest
from pydantic import SecretStr, ValidationError
from pydantic_settings import SettingsConfigDict

from app.ai.llm.base import (
    LLMAuthError,
    LLMError,
    LLMIncompleteError,
    LLMProvider,
    LLMRefusalError,
    LLMRequestError,
    LLMResult,
    LLMUnavailableError,
    Message,
    Usage,
)
from app.ai.llm.config import LLMConfig, LLMConfigError, create_provider, load_llm_config
from app.ai.llm.cost import (
    CostLedger,
    CostLimitExceededError,
    ModelPrice,
    call_cost,
    estimate_input_tokens,
    guard_call,
)
from app.ai.llm.fake_provider import FakeProvider
from app.ai.llm.openai_provider import OpenAIProvider
from app.core.config import Settings

pytestmark = pytest.mark.anyio

REPO = Path(__file__).resolve().parents[4]
CONFIG = REPO / "config" / "llm.yaml"
TEMPLATE = REPO / "skills" / "_template"
SCHEMA: dict[str, Any] = json.loads(
    (TEMPLATE / "schemas" / "output.schema.json").read_text(encoding="utf-8")
)
MESSAGES = [
    Message("system", "你是分類器。"),
    Message("user", '<untrusted_source id="e1">停水</untrusted_source>'),
]
OUTPUT = {
    "status": "ok",
    "announcement_type": "maintenance",
    "evidence_ids": ["e1"],
    "reason": "停水公告。",
    "confidence": 0.9,
    "abstain_reason": None,
}
KEY = "sk-test-0123456789abcdef"


class IsolatedSettings(Settings):
    """不讀任何 .env 檔，只看環境變數（沿用 test_db_config 的做法）。"""

    model_config = SettingsConfigDict(**{**Settings.model_config, "env_file": None})


def settings(monkeypatch: pytest.MonkeyPatch, key: str | None) -> Settings:
    monkeypatch.delenv("NCU_OPENAI_API_KEY", raising=False)
    if key is None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    else:
        monkeypatch.setenv("OPENAI_API_KEY", key)
    return IsolatedSettings()


MODEL = "model-under-test"  # 測試不使用真實型號


def completed(text: str, **usage: int) -> dict[str, Any]:
    return {
        "id": "resp_123",
        "object": "response",
        "status": "completed",
        "model": MODEL + "-2026-01-01",
        "output": [
            {"type": "reasoning", "id": "rs_1", "summary": []},
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            },
        ],
        "usage": {
            "input_tokens": usage.get("input", 120),
            "input_tokens_details": {"cached_tokens": usage.get("cached", 20)},
            "output_tokens": usage.get("output", 60),
            "output_tokens_details": {"reasoning_tokens": usage.get("reasoning", 40)},
            "total_tokens": usage.get("input", 120) + usage.get("output", 60),
        },
    }


class Recorder:
    def __init__(self, response: httpx2.Response | Exception) -> None:
        self.response = response
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    @property
    def body(self) -> dict[str, Any]:
        data: dict[str, Any] = json.loads(self.requests[-1].content)
        return data


def provider(handler: Recorder, **kwargs: Any) -> OpenAIProvider:
    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    return OpenAIProvider(SecretStr(KEY), client=client, **kwargs)


async def call(p: LLMProvider, **kwargs: Any) -> LLMResult:
    return await p.generate_structured(
        MESSAGES,
        SCHEMA,
        schema_name=kwargs.pop("schema_name", "template_output"),
        model=kwargs.pop("model", MODEL),
        timeout=kwargs.pop("timeout", 30.0),
        max_output_tokens=kwargs.pop("max_output_tokens", 1000),
    )


# --- 設定 ----------------------------------------------------------------------------------


def test_repo_config_loads_user_decisions() -> None:
    cfg = load_llm_config(CONFIG)
    assert cfg.provider == "openai"
    assert cfg.default_model in cfg.pricing
    assert cfg.limits.per_call_usd == 0.05
    assert cfg.limits.daily_usd == 1.0


def test_config_rejects_unknown_field(tmp_path: Path) -> None:
    text = CONFIG.read_text(encoding="utf-8") + "\ntemperature: 0.2\n"
    path = tmp_path / "llm.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(LLMConfigError):
        load_llm_config(path)


def test_config_requires_price_for_default_model() -> None:
    data = load_llm_config(CONFIG).model_dump()
    data["default_model"] = "unpriced-model"
    with pytest.raises(ValidationError):
        LLMConfig.model_validate(data)


def test_model_names_only_in_config() -> None:
    app = REPO / "backend" / "app"
    pattern = re.compile(r"\b(gpt-\d|o\d-|o\d\b|claude-|gemini-)")
    offenders = [
        f"{p.relative_to(app)}:{n}"
        for p in app.rglob("*.py")
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line)
    ]
    assert offenders == []


def test_create_provider_openai_requires_key(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = load_llm_config(CONFIG)
    with pytest.raises(LLMConfigError, match="OPENAI_API_KEY"):
        create_provider(cfg, settings(monkeypatch, None))


def test_create_provider_openai_with_key(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = load_llm_config(CONFIG)
    p = create_provider(cfg, settings(monkeypatch, KEY))
    assert isinstance(p, OpenAIProvider)


def test_create_provider_fake_needs_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = load_llm_config(CONFIG).model_copy(update={"provider": "fake"})
    p = create_provider(cfg, settings(monkeypatch, None))
    assert isinstance(p, FakeProvider)


def test_settings_reads_openai_api_key_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    s = settings(monkeypatch, KEY)
    assert s.openai_api_key is not None
    assert s.openai_api_key.get_secret_value() == KEY
    assert KEY not in repr(s)


# --- 費用 ----------------------------------------------------------------------------------

PRICE = ModelPrice(input=0.25, cached_input=0.025, output=2.0)


def test_call_cost_uses_cached_rate() -> None:
    usage = Usage(input_tokens=1_000_000, output_tokens=1_000_000, cached_input_tokens=400_000)
    # 60 萬 × 0.25 + 40 萬 × 0.025 + 100 萬 × 2.0
    assert call_cost(usage, PRICE) == pytest.approx(0.15 + 0.01 + 2.0)


def test_call_cost_without_cached_rate_charges_full_input() -> None:
    price = ModelPrice(input=1.0, cached_input=None, output=1.0)
    usage = Usage(input_tokens=1_000_000, output_tokens=0, cached_input_tokens=500_000)
    assert call_cost(usage, price) == pytest.approx(1.0)


def test_estimate_is_an_upper_bound_on_utf8_bytes() -> None:
    est = estimate_input_tokens(MESSAGES, SCHEMA)
    size = sum(len(m.content.encode()) for m in MESSAGES)
    size += len(json.dumps(SCHEMA, ensure_ascii=False).encode())
    assert est >= size


def test_guard_allows_small_call() -> None:
    estimate = guard_call(MESSAGES, SCHEMA, PRICE, max_output_tokens=4000, per_call_usd=0.05)
    assert 0 < estimate <= 0.05


def test_guard_blocks_call_over_per_call_limit() -> None:
    long = [Message("user", "長" * 60_000)]  # 18 萬 bytes ≈ US$0.045 輸入上限 + 輸出
    with pytest.raises(CostLimitExceededError, match="每次"):
        guard_call(long, SCHEMA, PRICE, max_output_tokens=4000, per_call_usd=0.05)


def test_ledger_blocks_when_daily_budget_would_be_exceeded() -> None:
    ledger = CostLedger(daily_usd=1.0, spent_usd=0.98)
    ledger.check(0.01)
    with pytest.raises(CostLimitExceededError, match="每日"):
        ledger.check(0.03)


def test_ledger_records_spending() -> None:
    ledger = CostLedger(daily_usd=1.0)
    ledger.record(0.4)
    ledger.record(0.5)
    assert ledger.spent_usd == pytest.approx(0.9)
    with pytest.raises(CostLimitExceededError):
        ledger.check(0.2)


def test_cost_limit_is_not_retryable() -> None:
    assert CostLimitExceededError("x").retryable is False


# --- OpenAI provider -----------------------------------------------------------------------


async def test_request_uses_strict_json_schema() -> None:
    rec = Recorder(httpx2.Response(200, json=completed(json.dumps(OUTPUT))))
    await call(provider(rec, reasoning_effort="low"))
    req = rec.requests[0]
    assert req.method == "POST"
    assert str(req.url) == "https://api.openai.com/v1/responses"
    assert req.headers["Authorization"] == f"Bearer {KEY}"
    body = rec.body
    assert body["model"] == MODEL
    assert body["input"] == [{"role": m.role, "content": m.content} for m in MESSAGES]
    assert body["text"] == {
        "format": {
            "type": "json_schema",
            "name": "template_output",
            "schema": SCHEMA,
            "strict": True,
        }
    }
    assert body["max_output_tokens"] == 1000
    assert body["store"] is False
    assert body["reasoning"] == {"effort": "low"}


async def test_request_omits_reasoning_when_not_configured() -> None:
    rec = Recorder(httpx2.Response(200, json=completed(json.dumps(OUTPUT))))
    await call(provider(rec))
    assert "reasoning" not in rec.body


async def test_request_passes_timeout() -> None:
    rec = Recorder(httpx2.Response(200, json=completed(json.dumps(OUTPUT))))
    await call(provider(rec), timeout=12.5)
    timeout = rec.requests[0].extensions["timeout"]
    assert timeout["read"] == 12.5


async def test_completed_response_is_parsed() -> None:
    rec = Recorder(
        httpx2.Response(200, json=completed(json.dumps(OUTPUT), input=500, cached=100, output=80))
    )
    result = await call(provider(rec))
    assert json.loads(result.text) == OUTPUT
    assert result.provider == "openai"
    assert result.model == MODEL + "-2026-01-01"
    assert result.response_id == "resp_123"
    assert result.usage == Usage(
        input_tokens=500, output_tokens=80, cached_input_tokens=100, reasoning_tokens=40
    )
    assert result.latency_ms >= 0


async def test_refusal_raises() -> None:
    body = completed("")
    body["output"][1]["content"] = [{"type": "refusal", "refusal": "I can't help with that."}]
    with pytest.raises(LLMRefusalError) as info:
        await call(provider(Recorder(httpx2.Response(200, json=body))))
    assert info.value.retryable is False


async def test_incomplete_response_raises() -> None:
    body = completed('{"status": ')
    body["status"] = "incomplete"
    body["incomplete_details"] = {"reason": "max_output_tokens"}
    with pytest.raises(LLMIncompleteError, match="max_output_tokens") as info:
        await call(provider(Recorder(httpx2.Response(200, json=body))))
    assert info.value.usage is not None and info.value.usage.output_tokens == 60


async def test_response_without_text_raises() -> None:
    body = completed("")
    body["output"] = [{"type": "reasoning", "id": "rs_1", "summary": []}]
    with pytest.raises(LLMUnavailableError):
        await call(provider(Recorder(httpx2.Response(200, json=body))))


async def test_body_not_json_raises_retryable() -> None:
    with pytest.raises(LLMUnavailableError) as info:
        await call(provider(Recorder(httpx2.Response(200, text="<html>bad gateway</html>"))))
    assert info.value.retryable is True


def error_body(code: str | None, message: str = "error message") -> dict[str, Any]:
    return {"error": {"message": message, "type": "invalid_request_error", "code": code}}


@pytest.mark.parametrize(
    ("status", "code", "exc", "retryable"),
    [
        (401, "invalid_api_key", LLMAuthError, False),
        (403, None, LLMAuthError, False),
        (400, "invalid_json_schema", LLMRequestError, False),
        (404, "model_not_found", LLMRequestError, False),
        (429, "rate_limit_exceeded", LLMUnavailableError, True),
        (429, "insufficient_quota", LLMRequestError, False),
        (408, None, LLMUnavailableError, True),
        (500, None, LLMUnavailableError, True),
        (503, None, LLMUnavailableError, True),
    ],
)
async def test_http_errors_are_classified(
    status: int, code: str | None, exc: type[LLMError], retryable: bool
) -> None:
    rec = Recorder(httpx2.Response(status, json=error_body(code)))
    with pytest.raises(exc) as info:
        await call(provider(rec))
    assert info.value.retryable is retryable
    assert info.value.status_code == status


async def test_retry_after_header_is_kept() -> None:
    rec = Recorder(
        httpx2.Response(429, json=error_body("rate_limit_exceeded"), headers={"retry-after": "7"})
    )
    with pytest.raises(LLMUnavailableError) as info:
        await call(provider(rec))
    assert info.value.retry_after == 7.0


@pytest.mark.parametrize(
    "error",
    [
        httpx2.ReadTimeout("timed out"),
        httpx2.ConnectError("no route"),
        httpx2.RemoteProtocolError("x"),
    ],
)
async def test_transport_errors_are_retryable(error: Exception) -> None:
    with pytest.raises(LLMUnavailableError) as info:
        await call(provider(Recorder(error)))
    assert info.value.retryable is True


async def test_api_key_never_appears_in_errors_or_repr() -> None:
    rec = Recorder(httpx2.Response(401, json=error_body("invalid_api_key", f"Incorrect key {KEY}")))
    p = provider(rec)
    with pytest.raises(LLMAuthError) as info:
        await call(p)
    assert KEY not in str(info.value)
    assert KEY not in repr(p)


async def test_long_error_message_is_truncated() -> None:
    rec = Recorder(httpx2.Response(400, json=error_body("bad", "x" * 5000)))
    with pytest.raises(LLMRequestError) as info:
        await call(provider(rec))
    assert len(str(info.value)) < 600


def test_openai_provider_satisfies_protocol() -> None:
    assert isinstance(OpenAIProvider(SecretStr(KEY)), LLMProvider)


# --- fake provider -------------------------------------------------------------------------


async def test_fake_returns_scripted_outputs_in_order() -> None:
    fake = FakeProvider([OUTPUT, "{not json"])
    first = await call(fake)
    second = await call(fake)
    assert json.loads(first.text) == OUTPUT
    assert second.text == "{not json"
    assert first.provider == "fake"


async def test_fake_raises_scripted_errors() -> None:
    fake = FakeProvider([LLMUnavailableError("逾時")])
    with pytest.raises(LLMUnavailableError):
        await call(fake)


async def test_fake_raises_when_exhausted() -> None:
    fake = FakeProvider([])
    with pytest.raises(LLMError, match="錄製回應"):
        await call(fake)


async def test_fake_records_calls() -> None:
    fake = FakeProvider([OUTPUT])
    await call(fake, model="m1", max_output_tokens=321)
    [c] = fake.calls
    assert c.messages == tuple(MESSAGES)
    assert c.json_schema == SCHEMA
    assert c.model == "m1"
    assert c.max_output_tokens == 321


async def test_fake_from_recorded_template_case() -> None:
    fake = FakeProvider.from_recorded(TEMPLATE / "tests" / "recorded" / "normal-001.json")
    recorded = json.loads(
        (TEMPLATE / "tests" / "recorded" / "normal-001.json").read_text(encoding="utf-8")
    )
    result = await call(fake)
    assert json.loads(result.text) == recorded["output"]
    assert result.model == recorded["model"]


def test_fake_satisfies_protocol() -> None:
    assert isinstance(FakeProvider([]), LLMProvider)
