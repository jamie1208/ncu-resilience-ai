"""事件向量與候選召回（P2-T09，S04 event-linker 的前一步）。

流程：新文章 → `embedding_text` → `embed_texts` 取得向量 → `find_candidate_events` 找出可能是
同一事件的既有事件 → 每個候選再交給 S04 判斷 same_event／related_event／none。

- 模型名稱與維度只寫在 `config/llm.yaml` 的 `embedding`；維度必須等於 `events.embedding`。
- 候選召回是確定性的程式：類別相同（可不限）、事件日期在視窗內、不是已封存的事件，
  依 cosine 距離排序。地點不在這裡過濾：S02 的地點寫法可能不同，由 S04 判斷。
- 送出前以 UTF-8 位元組數估算 token 上限，超過每次費用上限就不送（與 `guard_call` 相同的原則）。
"""

from __future__ import annotations

import uuid
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import Date, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm.base import EmbeddingProvider
from app.ai.llm.config import LLMConfig, LLMConfigError
from app.ai.llm.cost import PER_MILLION, CostLimitExceededError
from app.domain.states import EventStatus
from app.models.event import EVENT_EMBEDDING_DIM, Event, Location

MAX_EMBED_CHARS = 2000
"""送去 embedding 的文字上限；公告的重點通常在開頭。"""
DEFAULT_WINDOW_DAYS = 7
"""事件日期與新文章日期相差幾天內才算候選。

標記規範中社群來源的同事件是 48 小時內；這裡放寬，避免召回漏掉。
"""
DEFAULT_LIMIT = 5
TIMEZONE = "Asia/Taipei"


@dataclass(frozen=True)
class Embeddings:
    """`embed_texts` 的結果；`vectors` 與輸入順序相同。"""

    model: str
    vectors: tuple[tuple[float, ...], ...]
    input_tokens: int
    cost_usd: float


@dataclass(frozen=True)
class Candidate:
    """一個候選事件；`distance` 是 cosine 距離（0 表示方向相同）。"""

    event_id: uuid.UUID
    title: str
    category: str
    occurrence_start_date: date | None
    location: str | None
    distance: float


def embedding_text(title: str | None, text: str, *, max_chars: int = MAX_EMBED_CHARS) -> str:
    """標題與內文合併成一段文字（連續空白合併為一個空格），並截斷到 `max_chars`。"""
    parts = [" ".join(p.split()) for p in (title or "", text)]
    return "\n".join(p for p in parts if p)[:max_chars]


async def embed_texts(
    provider: EmbeddingProvider, texts: Sequence[str], *, config: LLMConfig
) -> Embeddings:
    """依設定取得向量並計算費用；沒有 embedding 設定、維度不符或超過每次上限時拋出錯誤。"""
    if config.embedding is None:
        raise LLMConfigError("config/llm.yaml 沒有設定 embedding")
    if config.embedding.dimensions != EVENT_EMBEDDING_DIM:
        raise LLMConfigError(
            f"embedding 維度 {config.embedding.dimensions} 與 events.embedding "
            f"的維度 {EVENT_EMBEDDING_DIM} 不符"
        )
    model = config.embedding.model
    if not texts:
        return Embeddings(model, (), 0, 0.0)
    price = config.price(model)
    estimate = sum(len(t.encode("utf-8")) for t in texts) * price.input / PER_MILLION
    if estimate > config.limits.per_call_usd:
        raise CostLimitExceededError(
            f"embedding 預估費用 US${estimate:.6f} 超過每次上限 US${config.limits.per_call_usd}"
        )
    result = await provider.embed(
        texts, model=model, dimensions=config.embedding.dimensions, timeout=config.timeout_seconds
    )
    cost = result.input_tokens * price.input / PER_MILLION
    return Embeddings(model, result.vectors, result.input_tokens, cost)


async def find_candidate_events(
    session: AsyncSession,
    embedding: Sequence[float],
    *,
    on_date: date,
    category: str | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    limit: int = DEFAULT_LIMIT,
    max_distance: float | None = None,
    exclude: Collection[uuid.UUID] = (),
) -> list[Candidate]:
    """找出可能與新文章是同一事件的既有事件，依 cosine 距離由近到遠排列。

    事件沒有 `occurrence_start_date` 時，以建立時間的台北日期代替。
    """
    if len(embedding) != EVENT_EMBEDDING_DIM:
        raise ValueError(f"向量維度 {len(embedding)} 與 events.embedding 的維度不符")
    distance = Event.embedding.cosine_distance(list(embedding)).label("distance")
    event_day = func.coalesce(
        Event.occurrence_start_date, cast(func.timezone(TIMEZONE, Event.created_at), Date)
    )
    window = timedelta(days=window_days)
    stmt = (
        select(Event, Location.name, distance)
        .outerjoin(Location, Location.id == Event.location_id)
        .where(
            Event.embedding.is_not(None),
            Event.status != EventStatus.ARCHIVED.value,
            event_day.between(on_date - window, on_date + window),
        )
        .order_by(distance, Event.id)
        .limit(limit)
    )
    if category is not None:
        stmt = stmt.where(Event.type == category)
    if exclude:
        stmt = stmt.where(Event.id.not_in(list(exclude)))
    if max_distance is not None:
        stmt = stmt.where(distance <= max_distance)
    rows = (await session.execute(stmt)).all()
    return [
        Candidate(
            event_id=event.id,
            title=event.title,
            category=event.type,
            occurrence_start_date=event.occurrence_start_date,
            location=location,
            distance=float(dist),
        )
        for event, location, dist in rows
    ]
