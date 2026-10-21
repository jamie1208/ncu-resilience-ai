"""P2-T09：Embedding（OpenAI embeddings、fake、設定、費用檢查、輸入文字）。

- OpenAI 以 httpx2 呼叫 `/v1/embeddings`；測試用 MockTransport，不連網。
- 模型名稱與維度只出現在 `config/llm.yaml`（使用者 2026-10-21 決定 text-embedding-3-small）。
- 向量維度必須等於 `events.embedding` 欄位的維度。
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import httpx2
import pytest
from pydantic import SecretStr, ValidationError
from pydantic_settings import SettingsConfigDict

from app.ai.embeddings import embed_texts, embedding_text
from app.ai.llm.base import (
    EmbeddingProvider,
    LLMAuthError,
    LLMRequestError,
    LLMUnavailableError,
)
from app.ai.llm.config import LLMConfig, LLMConfigError, create_embedding_provider, load_llm_config
from app.ai.llm.cost import CostLimitExceededError
from app.ai.llm.fake_provider import FakeEmbeddingProvider
from app.ai.llm.openai_provider import OpenAIProvider
from app.core.config import Settings
from app.models.event import EVENT_EMBEDDING_DIM

pytestmark = pytest.mark.anyio

REPO = Path(__file__).resolve().parents[4]
CONFIG = REPO / "config" / "llm.yaml"
KEY = "sk-test-0123456789abcdef"
MODEL = "embed-model"


class IsolatedSettings(Settings):
    """不讀任何 .env 檔，只看環境變數（沿用 test_llm_adapter 的做法）。"""

    model_config = SettingsConfigDict(**{**Settings.model_config, "env_file": None})


def settings(monkeypatch: pytest.MonkeyPatch, key: str | None) -> Settings:
    monkeypatch.delenv("NCU_OPENAI_API_KEY", raising=False)
    if key is None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    else:
        monkeypatch.setenv("OPENAI_API_KEY", key)
    return IsolatedSettings()


def embedding_response(n: int, dims: int, *, tokens: int = 42) -> dict[str, Any]:
    # 故意倒序回傳，確認依 index 排序
    return {
        "object": "list",
        "model": MODEL + "-v1",
        "data": [
            {"object": "embedding", "index": i, "embedding": [float(i)] * dims}
            for i in reversed(range(n))
        ],
        "usage": {"prompt_tokens": tokens, "total_tokens": tokens},
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


def provider(handler: Recorder) -> OpenAIProvider:
    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    return OpenAIProvider(SecretStr(KEY), client=client)


def config(**embedding: Any) -> LLMConfig:
    data = load_llm_config(CONFIG).model_dump()
    data["embedding"] = {**data["embedding"], **embedding}
    return LLMConfig.model_validate(data)


# --- 設定 ----------------------------------------------------------------------------------


def test_repo_config_has_embedding_model_with_price() -> None:
    cfg = load_llm_config(CONFIG)
    assert cfg.embedding is not None
    assert cfg.embedding.dimensions == EVENT_EMBEDDING_DIM
    assert cfg.embedding.model in cfg.pricing


def test_embedding_model_requires_price() -> None:
    data = load_llm_config(CONFIG).model_dump()
    data["embedding"]["model"] = "unpriced-embedding"
    with pytest.raises(ValidationError):
        LLMConfig.model_validate(data)


def test_create_embedding_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = load_llm_config(CONFIG)
    assert isinstance(create_embedding_provider(cfg, settings(monkeypatch, KEY)), OpenAIProvider)
    fake = cfg.model_copy(update={"provider": "fake"})
    assert isinstance(
        create_embedding_provider(fake, settings(monkeypatch, None)), FakeEmbeddingProvider
    )


def test_create_embedding_provider_requires_key(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(LLMConfigError, match="OPENAI_API_KEY"):
        create_embedding_provider(load_llm_config(CONFIG), settings(monkeypatch, None))


# --- OpenAI embeddings ---------------------------------------------------------------------


async def test_openai_embed_request_and_parse() -> None:
    rec = Recorder(httpx2.Response(200, json=embedding_response(2, 3)))
    result = await provider(rec).embed(["甲", "乙"], model=MODEL, dimensions=3, timeout=10.0)
    assert str(rec.requests[0].url) == "https://api.openai.com/v1/embeddings"
    assert rec.requests[0].headers["authorization"] == f"Bearer {KEY}"
    assert rec.body == {
        "model": MODEL,
        "input": ["甲", "乙"],
        "dimensions": 3,
        "encoding_format": "float",
    }
    assert result.vectors == ((0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
    assert result.input_tokens == 42
    assert result.model == MODEL + "-v1"
    assert result.provider == "openai"


async def test_openai_embed_rejects_wrong_count_or_dimensions() -> None:
    rec = Recorder(httpx2.Response(200, json=embedding_response(1, 3)))
    with pytest.raises(LLMUnavailableError, match="數量"):
        await provider(rec).embed(["甲", "乙"], model=MODEL, dimensions=3, timeout=10.0)
    rec = Recorder(httpx2.Response(200, json=embedding_response(1, 2)))
    with pytest.raises(LLMUnavailableError, match="維度"):
        await provider(rec).embed(["甲"], model=MODEL, dimensions=3, timeout=10.0)


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (401, LLMAuthError),
        (400, LLMRequestError),
        (429, LLMUnavailableError),
        (503, LLMUnavailableError),
    ],
)
async def test_openai_embed_http_errors_are_classified(status: int, error: type[Exception]) -> None:
    body = {"error": {"code": None, "message": f"bad {KEY}"}}
    rec = Recorder(httpx2.Response(status, json=body))
    with pytest.raises(error) as info:
        await provider(rec).embed(["甲"], model=MODEL, dimensions=3, timeout=10.0)
    assert KEY not in str(info.value)


async def test_openai_embed_transport_error_is_retryable() -> None:
    rec = Recorder(httpx2.ConnectError("boom"))
    with pytest.raises(LLMUnavailableError):
        await provider(rec).embed(["甲"], model=MODEL, dimensions=3, timeout=10.0)


def test_providers_satisfy_embedding_protocol() -> None:
    assert isinstance(OpenAIProvider(SecretStr(KEY)), EmbeddingProvider)
    assert isinstance(FakeEmbeddingProvider(), EmbeddingProvider)


# --- Fake ----------------------------------------------------------------------------------


async def test_fake_embedding_is_deterministic_and_normalized() -> None:
    fake = FakeEmbeddingProvider()
    texts = ["工五停電", "工五停電", "女二舍失竊"]
    a = await fake.embed(texts, model=MODEL, dimensions=8, timeout=1)
    assert a.vectors[0] == a.vectors[1]
    assert a.vectors[0] != a.vectors[2]
    assert all(len(v) == 8 for v in a.vectors)
    assert math.isclose(math.sqrt(sum(x * x for x in a.vectors[0])), 1.0)
    assert fake.calls == [("工五停電", "工五停電", "女二舍失竊")]


async def test_fake_embedding_uses_fixed_vectors() -> None:
    fake = FakeEmbeddingProvider({"甲": [1.0, 0.0]})
    result = await fake.embed(["甲"], model=MODEL, dimensions=2, timeout=1)
    assert result.vectors == ((1.0, 0.0),)


# --- embed_texts ---------------------------------------------------------------------------


async def test_embed_texts_uses_config_and_counts_cost() -> None:
    cfg = load_llm_config(CONFIG)
    assert cfg.embedding is not None
    fake = FakeEmbeddingProvider()
    result = await embed_texts(fake, ["工五停電"], config=cfg)
    assert len(result.vectors) == 1 and len(result.vectors[0]) == EVENT_EMBEDDING_DIM
    price = cfg.pricing[cfg.embedding.model].input
    assert result.cost_usd == pytest.approx(result.input_tokens * price / 1_000_000)
    assert result.model == cfg.embedding.model


async def test_embed_texts_rejects_dimension_mismatch() -> None:
    with pytest.raises(LLMConfigError, match="維度"):
        await embed_texts(FakeEmbeddingProvider(), ["甲"], config=config(dimensions=256))


async def test_embed_texts_blocks_call_over_per_call_limit() -> None:
    cfg = load_llm_config(CONFIG)
    tiny = cfg.model_copy(update={"limits": cfg.limits.model_copy(update={"per_call_usd": 1e-9})})
    fake = FakeEmbeddingProvider()
    with pytest.raises(CostLimitExceededError):
        await embed_texts(fake, ["工五停電" * 100], config=tiny)
    assert fake.calls == []


async def test_embed_texts_requires_embedding_config() -> None:
    cfg = load_llm_config(CONFIG).model_copy(update={"embedding": None})
    with pytest.raises(LLMConfigError, match="embedding"):
        await embed_texts(FakeEmbeddingProvider(), ["甲"], config=cfg)


async def test_embed_texts_empty_input_makes_no_call() -> None:
    fake = FakeEmbeddingProvider()
    result = await embed_texts(fake, [], config=load_llm_config(CONFIG))
    assert result.vectors == () and result.cost_usd == 0.0
    assert fake.calls == []


# --- embedding_text ------------------------------------------------------------------------


def test_embedding_text_joins_title_and_collapses_whitespace() -> None:
    assert embedding_text("工五停電", "  明天\n\n  停電  ") == "工五停電\n明天 停電"
    assert embedding_text(None, "內文") == "內文"
    assert embedding_text("標題", "") == "標題"


def test_embedding_text_is_truncated() -> None:
    assert len(embedding_text("標", "字" * 5000, max_chars=100)) == 100
