"""P1-T11：氣象署開放資料 Adapter（固定 fixture，CI 不呼叫真實 API）。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx2
import pytest
from pydantic import SecretStr
from pydantic_settings import SettingsConfigDict

from app.core.config import Settings
from app.ingestion.adapters.cwa_open_data import (
    API_BASE,
    EARTHQUAKES,
    WARNINGS,
    CwaOpenDataAdapter,
)
from app.ingestion.base import (
    AdapterError,
    LayoutChangedError,
    SourceAdapter,
    SourceUnavailableError,
)
from app.ingestion.http import USER_AGENT, HostRateLimiter
from app.ingestion.registry import registry
from app.ingestion.types import AccessPolicy, RawItem, SourceConfig

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "cwa"
FAKE_KEY = "TEST-KEY-not-a-real-authorization-code"
NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)

pytestmark = pytest.mark.anyio

Handler = Callable[[httpx2.Request], httpx2.Response]


def body(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class FakeApi:
    """依資料集代碼回應 fixture；記錄所有請求。"""

    def __init__(self, warnings: str = "warnings_taoyuan.json", quakes: str = "earthquakes.json"):
        self.responses: dict[str, Handler] = {
            WARNINGS: self.json(warnings),
            EARTHQUAKES: self.json(quakes),
        }
        self.requests: list[httpx2.Request] = []

    @staticmethod
    def json(name: str, status: int = 200) -> Handler:
        return lambda request: httpx2.Response(
            status, content=body(name), headers={"content-type": "application/json"}
        )

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        dataset = request.url.path.rsplit("/", 1)[-1]
        return self.responses[dataset](request)


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


def config(**options: Any) -> SourceConfig:
    return SourceConfig(
        code="cwa-alerts",
        type="cwa_open_data",
        access_policy=AccessPolicy.APPROVED,
        enabled=True,
        options={"county": "桃園市", **options},
    )


def make(api: FakeApi, *, key: str | None = FAKE_KEY, **options: Any) -> CwaOpenDataAdapter:
    t = FakeTime()
    return CwaOpenDataAdapter(
        config(**options),
        client=httpx2.AsyncClient(transport=httpx2.MockTransport(api)),
        limiter=HostRateLimiter(clock=t.clock, sleep=t.sleep),
        clock=lambda: NOW,
        api_key=lambda: SecretStr(key) if key is not None else None,
    )


def by_id(items: list[RawItem]) -> dict[str, RawItem]:
    return {i.external_id or "": i for i in items}


# --- 天氣警特報 ------------------------------------------------------------------------


async def test_warnings_become_items_per_hazard() -> None:
    items = await make(FakeApi(), datasets=[WARNINGS]).fetch_since(None)
    assert len(items) == 2
    got = by_id(items)
    rain = got["W-C0033-001:桃園市:大雨特報:2026-10-17T08:30:00+08:00"]
    assert rain.source_code == "cwa-alerts"
    assert rain.title == "桃園市大雨特報"
    assert rain.url == "https://www.cwa.gov.tw/V8/C/P/Warning/FIFOWS.html"
    assert rain.content_type == "text/plain"
    assert "中央氣象署發布桃園市大雨特報" in rain.content
    assert "2026-10-17 08:30 至 2026-10-17 20:00" in rain.content
    # 臺灣時間 08:30 → UTC 00:30
    assert rain.published_at == datetime(2026, 10, 17, 0, 30, tzinfo=UTC)
    assert rain.fetched_at == NOW


async def test_no_current_warnings() -> None:
    items = await make(FakeApi(warnings="warnings_none.json"), datasets=[WARNINGS]).fetch_since(
        None
    )
    assert items == []


async def test_warnings_for_other_counties_are_ignored() -> None:
    items = await make(FakeApi(), datasets=[WARNINGS], county="臺北市").fetch_since(None)
    assert items == []


# --- 地震報告 --------------------------------------------------------------------------


async def test_only_earthquakes_felt_in_county_are_kept() -> None:
    items = await make(FakeApi(), datasets=[EARTHQUAKES]).fetch_since(None)
    assert sorted(by_id(items)) == ["E-A0015-001:115901", "E-A0015-001:115902"]


async def test_earthquake_item_fields() -> None:
    items = await make(FakeApi(), datasets=[EARTHQUAKES]).fetch_since(None)
    quake = by_id(items)["E-A0015-001:115902"]
    assert quake.title == "顯著有感地震報告 第 115902 號"
    assert quake.url == "https://scweb.cwa.gov.tw/zh-tw/earthquake/details/2026902"
    assert "宜蘭縣近海發生規模5.6有感地震" in quake.content
    # 單一縣市的條目優先（3級），不採用分組條目
    assert "桃園市最大震度：3級" in quake.content
    assert "芮氏規模 5.6" in quake.content
    assert "深度 21.3 公里" in quake.content
    assert quake.published_at == datetime(2026, 10, 16, 19, 12, 45, tzinfo=UTC)


async def test_county_only_in_grouped_area_uses_group_intensity() -> None:
    items = await make(FakeApi(), datasets=[EARTHQUAKES]).fetch_since(None)
    assert "桃園市最大震度：1級" in by_id(items)["E-A0015-001:115901"].content


# --- 出處與冪等 ------------------------------------------------------------------------


async def test_every_item_carries_open_data_attribution() -> None:
    items = await make(FakeApi()).fetch_since(None)
    assert len(items) == 4
    for item in items:
        assert "資料來源：中央氣象署" in item.content
        assert "政府資料開放授權條款" in item.content
        assert "https://data.gov.tw/license" in item.content


async def test_ids_and_content_are_stable_across_runs() -> None:
    first = await make(FakeApi()).fetch_since(None)
    second = await make(FakeApi()).fetch_since(None)
    assert [(i.external_id, i.content) for i in first] == [
        (i.external_id, i.content) for i in second
    ]
    assert len({i.external_id for i in first}) == len(first)


async def test_cursor_is_not_used() -> None:
    adapter = make(FakeApi())
    items = await adapter.fetch_since("anything")
    assert adapter.next_cursor(items, "anything") == "anything"
    assert adapter.next_cursor(items, None) is None


# --- 請求與授權碼 ----------------------------------------------------------------------


async def test_requests_send_key_in_header_not_url() -> None:
    api = FakeApi()
    await make(api, earthquake_limit=15).fetch_since(None)
    assert len(api.requests) == 2
    for request in api.requests:
        assert request.headers["authorization"] == FAKE_KEY
        assert request.headers["user-agent"] == USER_AGENT
        assert FAKE_KEY not in str(request.url)
        assert "Authorization" not in request.url.params
        assert str(request.url).startswith(API_BASE)
    warnings, quakes = api.requests
    assert warnings.url.params["locationName"] == "桃園市"
    assert quakes.url.params["limit"] == "15"


async def test_missing_api_key_is_not_retryable_and_sends_nothing() -> None:
    api = FakeApi()
    with pytest.raises(AdapterError) as exc:
        await make(api, key=None).fetch_since(None)
    assert exc.value.retryable is False
    assert "CWA_API_KEY" in str(exc.value)
    assert api.requests == []


@pytest.mark.parametrize("status", [401, 403])
async def test_rejected_key_is_not_retryable_and_not_leaked(status: int) -> None:
    api = FakeApi()
    api.responses[WARNINGS] = FakeApi.json("unexpected.json", status)
    with pytest.raises(AdapterError) as exc:
        await make(api).fetch_since(None)
    assert exc.value.retryable is False
    assert FAKE_KEY not in str(exc.value)


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_server_errors_are_retryable(status: int) -> None:
    api = FakeApi()
    api.responses[EARTHQUAKES] = FakeApi.json("unexpected.json", status)
    with pytest.raises(SourceUnavailableError):
        await make(api).fetch_since(None)


async def test_timeout_is_retryable() -> None:
    def timeout(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.TimeoutException("timed out", request=request)

    api = FakeApi()
    api.responses[WARNINGS] = timeout
    with pytest.raises(SourceUnavailableError) as exc:
        await make(api).fetch_since(None)
    assert FAKE_KEY not in str(exc.value)


async def test_missing_records_is_layout_change() -> None:
    with pytest.raises(LayoutChangedError):
        await make(FakeApi(warnings="unexpected.json")).fetch_since(None)


async def test_unsuccessful_response_is_rejected() -> None:
    api = FakeApi()
    api.responses[WARNINGS] = lambda r: httpx2.Response(200, json={"success": "false"})
    with pytest.raises(AdapterError):
        await make(api).fetch_since(None)


async def test_requests_are_rate_limited() -> None:
    t = FakeTime()
    api = FakeApi()
    adapter = CwaOpenDataAdapter(
        config(),
        client=httpx2.AsyncClient(transport=httpx2.MockTransport(api)),
        limiter=HostRateLimiter(clock=t.clock, sleep=t.sleep),
        clock=lambda: NOW,
        api_key=lambda: SecretStr(FAKE_KEY),
    )
    await adapter.fetch_since(None)
    assert t.now == 5.0  # 兩個請求之間等待 5 秒


# --- 設定 ------------------------------------------------------------------------------


def test_cwa_type_is_registered() -> None:
    assert "cwa_open_data" in registry.types()
    created = registry.create(config())
    assert isinstance(created, CwaOpenDataAdapter)
    assert isinstance(created, SourceAdapter)


@pytest.mark.parametrize(
    "options",
    [
        {"county": ""},
        {"county": "桃園市", "datasets": []},
        {"county": "桃園市", "datasets": ["F-C0032-001"]},
        {"county": "桃園市", "earthquake_limit": 0},
        {"county": "桃園市", "earthquake_limit": 51},
        {"county": "桃園市", "api_key": "inline"},  # 授權碼不得寫在設定檔
    ],
)
def test_invalid_options_are_rejected(options: dict[str, Any]) -> None:
    cfg = SourceConfig(
        code="cwa-alerts",
        type="cwa_open_data",
        access_policy=AccessPolicy.APPROVED,
        enabled=True,
        options=options,
    )
    with pytest.raises(ValueError):
        CwaOpenDataAdapter(cfg)


class IsolatedSettings(Settings):
    """不讀任何 .env 檔，只看環境變數（沿用 test_db_config 的做法）。"""

    model_config = SettingsConfigDict(**{**Settings.model_config, "env_file": None})


def test_settings_read_cwa_api_key_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CWA_API_KEY", FAKE_KEY)
    settings = IsolatedSettings()
    assert settings.cwa_api_key is not None
    assert settings.cwa_api_key.get_secret_value() == FAKE_KEY
    assert FAKE_KEY not in repr(settings)


def test_settings_without_cwa_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CWA_API_KEY", raising=False)
    monkeypatch.delenv("NCU_CWA_API_KEY", raising=False)
    assert IsolatedSettings().cwa_api_key is None
