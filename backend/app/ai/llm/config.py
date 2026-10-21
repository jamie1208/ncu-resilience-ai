"""LLM 設定（`config/llm.yaml`，P2-T03）與 Provider 建立。

模型名稱、價格與費用上限只寫在設定檔；金鑰只從環境變數讀取（`Settings.openai_api_key`）。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    PositiveFloat,
    PositiveInt,
    ValidationError,
    model_validator,
)

from app.ai.llm.base import EmbeddingProvider, LLMProvider
from app.ai.llm.cost import ModelPrice
from app.ai.llm.fake_provider import FakeEmbeddingProvider, FakeProvider
from app.ai.llm.openai_provider import OpenAIProvider
from app.core.config import Settings

DEFAULT_CONFIG = Path(__file__).resolve().parents[4] / "config" / "llm.yaml"


class LLMConfigError(ValueError):
    """設定檔不存在、格式錯誤，或缺少必要的金鑰。"""


class Limits(BaseModel):
    """費用上限（USD）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    per_call_usd: PositiveFloat
    daily_usd: PositiveFloat


class EmbeddingConfig(BaseModel):
    """Embedding 模型（P2-T09，使用者 2026-10-21 決定）。

    `dimensions` 必須等於 events.embedding 的維度。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str
    dimensions: PositiveInt


class LLMConfig(BaseModel):
    """`config/llm.yaml` 的內容。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: PositiveInt
    provider: Literal["openai", "fake"]
    default_model: str
    timeout_seconds: PositiveFloat
    max_output_tokens: PositiveInt
    reasoning_effort: Literal["minimal", "low", "medium", "high"] | None = None
    limits: Limits
    pricing_checked_at: date
    pricing: dict[str, ModelPrice]
    embedding: EmbeddingConfig | None = None

    @model_validator(mode="after")
    def _priced(self) -> Self:
        if self.default_model not in self.pricing:
            raise ValueError(f"default_model {self.default_model!r} 沒有對應的 pricing")
        if self.embedding is not None and self.embedding.model not in self.pricing:
            raise ValueError(f"embedding.model {self.embedding.model!r} 沒有對應的 pricing")
        return self

    def price(self, model: str) -> ModelPrice:
        """模型的價格；沒有價格的模型不允許呼叫（無法記帳）。"""
        try:
            return self.pricing[model]
        except KeyError:
            raise LLMConfigError(
                f"模型 {model!r} 沒有設定價格（config/llm.yaml pricing）"
            ) from None


def load_llm_config(path: Path = DEFAULT_CONFIG) -> LLMConfig:
    """讀取並驗證設定檔。"""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise LLMConfigError(f"無法讀取 {path}：{exc}") from exc
    try:
        return LLMConfig.model_validate(raw)
    except ValidationError as exc:
        raise LLMConfigError(f"{path} 格式錯誤：{exc}") from exc


def _openai(config: LLMConfig, settings: Settings) -> OpenAIProvider:
    if settings.openai_api_key is None or not settings.openai_api_key.get_secret_value():
        raise LLMConfigError("provider 為 openai，但沒有設定環境變數 OPENAI_API_KEY")
    return OpenAIProvider(settings.openai_api_key, reasoning_effort=config.reasoning_effort)


def create_provider(config: LLMConfig, settings: Settings) -> LLMProvider:
    """依設定建立 Provider；openai 需要環境變數 OPENAI_API_KEY。"""
    if config.provider == "fake":
        return FakeProvider([])
    return _openai(config, settings)


def create_embedding_provider(config: LLMConfig, settings: Settings) -> EmbeddingProvider:
    """依設定建立 Embedding Provider（P2-T09）；openai 需要環境變數 OPENAI_API_KEY。"""
    if config.provider == "fake":
        return FakeEmbeddingProvider()
    return _openai(config, settings)
