"""P1-T01：Source Adapter 介面、RawItem 與 registry。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from app.ingestion.base import (
    AdapterError,
    LayoutChangedError,
    SourceAdapter,
    SourceUnavailableError,
)
from app.ingestion.registry import (
    DuplicateSourceTypeError,
    SourceNotApprovedError,
    SourceRegistry,
    UnknownSourceTypeError,
)
from app.ingestion.types import AccessPolicy, RawItem, SourceConfig

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)


def raw(**overrides: Any) -> RawItem:
    fields: dict[str, Any] = {
        "source_code": "ncu-announcements",
        "external_id": "2079",
        "url": "https://www.ncu.edu.tw/p/406-1000-2079,r15.php",
        "title": "停電公告",
        "content": "宿舍 10/18 停電",
        "content_type": "text/plain",
        "published_at": NOW - timedelta(hours=1),
        "fetched_at": NOW,
    }
    fields.update(overrides)
    return RawItem(**fields)


def config(**overrides: Any) -> SourceConfig:
    fields: dict[str, Any] = {
        "code": "cwa-alerts",
        "type": "fake",
        "access_policy": AccessPolicy.APPROVED,
        "enabled": True,
        "options": {"url": "https://example.invalid/feed"},
    }
    fields.update(overrides)
    return SourceConfig(**fields)


class FakeAdapter:
    """以時間為 cursor 的假 Adapter：只回傳發布時間晚於 cursor 的資料。"""

    def __init__(self, cfg: SourceConfig) -> None:
        self.code = cfg.code
        self.items = [
            raw(source_code=cfg.code, external_id=str(i), published_at=NOW - timedelta(hours=i))
            for i in range(3)
        ]

    async def fetch_since(self, cursor: str | None) -> list[RawItem]:
        if cursor is None:
            return list(self.items)
        since = datetime.fromisoformat(cursor)
        return [i for i in self.items if i.published_at and i.published_at > since]

    def next_cursor(self, items: Sequence[RawItem], previous: str | None) -> str | None:
        times = [i.published_at for i in items if i.published_at]
        return max(times).isoformat() if times else previous


# --- RawItem ------------------------------------------------------------------------


def test_raw_item_accepts_valid_data() -> None:
    item = raw()
    assert item.source_code == "ncu-announcements"
    assert item.fetched_at.utcoffset() == timedelta(0)


def test_raw_item_normalizes_times_to_utc() -> None:
    taipei = timezone(timedelta(hours=8))
    item = raw(published_at=datetime(2026, 10, 17, 20, 0, tzinfo=taipei))
    assert item.published_at == datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
    assert item.published_at is not None
    assert item.published_at.utcoffset() == timedelta(0)


@pytest.mark.parametrize("field", ["published_at", "fetched_at"])
def test_raw_item_rejects_naive_datetimes(field: str) -> None:
    with pytest.raises(ValueError):
        raw(**{field: datetime(2026, 10, 17, 12, 0)})


@pytest.mark.parametrize("url", ["ftp://x/y", "javascript:alert(1)", "/relative", ""])
def test_raw_item_requires_http_url(url: str) -> None:
    with pytest.raises(ValueError):
        raw(url=url)


@pytest.mark.parametrize("field", ["source_code", "content"])
def test_raw_item_rejects_empty_required_text(field: str) -> None:
    with pytest.raises(ValueError):
        raw(**{field: "  "})


def test_raw_item_published_at_and_external_id_optional() -> None:
    item = raw(published_at=None, external_id=None, title=None)
    assert item.published_at is None
    assert item.external_id is None


def test_raw_item_rejects_unknown_content_type() -> None:
    with pytest.raises(ValueError):
        raw(content_type="application/pdf")


def test_raw_item_is_immutable() -> None:
    item: Any = raw()  # 刻意繞過型別檢查，確認執行期也無法修改
    with pytest.raises(ValueError):
        item.title = "改過"


# --- Protocol 與錯誤 ----------------------------------------------------------------


def test_fake_adapter_satisfies_protocol() -> None:
    assert isinstance(FakeAdapter(config()), SourceAdapter)


def test_object_without_methods_is_not_an_adapter() -> None:
    assert not isinstance(object(), SourceAdapter)


def test_error_retryability() -> None:
    assert SourceUnavailableError("timeout").retryable is True
    assert LayoutChangedError("缺少標題欄位").retryable is False
    assert issubclass(LayoutChangedError, AdapterError)
    assert issubclass(SourceUnavailableError, AdapterError)


@pytest.mark.anyio
async def test_fetch_since_is_incremental() -> None:
    adapter = FakeAdapter(config())
    first = await adapter.fetch_since(None)
    cursor = adapter.next_cursor(first, None)
    assert len(first) == 3
    assert await adapter.fetch_since(cursor) == []
    assert adapter.next_cursor([], cursor) == cursor


# --- registry ------------------------------------------------------------------------


@pytest.fixture()
def registry() -> SourceRegistry:
    reg = SourceRegistry()
    reg.register("fake")(FakeAdapter)
    return reg


def test_create_returns_adapter_for_approved_enabled_source(registry: SourceRegistry) -> None:
    adapter = registry.create(config())
    assert isinstance(adapter, FakeAdapter)
    assert adapter.code == "cwa-alerts"


def test_registered_types(registry: SourceRegistry) -> None:
    assert registry.types() == frozenset({"fake"})


def test_unknown_type_is_rejected(registry: SourceRegistry) -> None:
    with pytest.raises(UnknownSourceTypeError, match="rss"):
        registry.create(config(type="rss"))


def test_duplicate_registration_is_rejected(registry: SourceRegistry) -> None:
    with pytest.raises(DuplicateSourceTypeError):
        registry.register("fake")(FakeAdapter)


@pytest.mark.parametrize(
    "policy", [AccessPolicy.PENDING, AccessPolicy.DEFERRED, AccessPolicy.REJECTED]
)
def test_only_approved_sources_can_be_created(
    registry: SourceRegistry, policy: AccessPolicy
) -> None:
    with pytest.raises(SourceNotApprovedError, match="cwa-alerts"):
        registry.create(config(access_policy=policy))


def test_disabled_source_cannot_be_created(registry: SourceRegistry) -> None:
    with pytest.raises(SourceNotApprovedError):
        registry.create(config(enabled=False))


def test_access_policy_values_are_stable() -> None:
    # sources.access_policy 以字串存入資料庫
    assert {p.value for p in AccessPolicy} == {"approved", "pending", "deferred", "rejected"}


def test_source_config_options_are_read_only() -> None:
    options: Any = config().options
    with pytest.raises(TypeError):
        options["url"] = "https://evil.invalid"


def test_access_policy_from_database_string_is_coerced(registry: SourceRegistry) -> None:
    # 從資料庫讀出的是一般字串
    raw_policy: Any = "approved"
    cfg = config(access_policy=raw_policy)
    assert cfg.access_policy is AccessPolicy.APPROVED
    assert isinstance(registry.create(cfg), FakeAdapter)


def test_invalid_access_policy_is_rejected() -> None:
    bad: Any = "核准"
    with pytest.raises(ValueError):
        config(access_policy=bad)
