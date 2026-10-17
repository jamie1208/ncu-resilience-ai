"""中央氣象署開放資料 Adapter（P1-T11，ADR-0005），來源類型 `cwa_open_data`。

資料集（`datasets`，預設兩者都抓）：
- W-C0033-001 天氣特報：各縣市目前生效的警特報；每一項警特報（類型＋等級＋開始時間）為一個項目。
- E-A0015-001 顯著有感地震報告：只保留震度分布包含指定縣市的地震（使用者 2026-10-17 決定）。

授權與出處：
- 授權碼只從環境變數 `CWA_API_KEY` 讀取，放在 HTTP 標頭 `Authorization`（平台同時接受 query 參數，
  但網址容易出現在日誌，所以不用）。授權碼不會出現在網址、錯誤訊息與項目內容中。
- 政府資料開放授權條款第 1 版要求標示提供機關、年份、資料名稱與授權條款網址，每個項目的內文最後
  都附上出處。

資料是「目前狀態」的快照，不使用 cursor；每輪回傳目前全部項目，重複由冪等寫入（P1-T06）處理。

來源設定（`SourceConfig.options`）：
- `county`（必填）：縣市名稱，例如「桃園市」
- `datasets`：要抓的資料集，預設兩者
- `earthquake_limit`：地震報告最多取最近幾筆（1–50，預設 20）
- `timeout`：單次請求秒數（預設 15）
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import httpx2
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from app.core.config import get_settings
from app.ingestion.base import AdapterError, LayoutChangedError, SourceUnavailableError
from app.ingestion.http import MIN_REQUEST_INTERVAL, USER_AGENT, HostRateLimiter, shared_limiter
from app.ingestion.registry import registry
from app.ingestion.types import RawItem, SourceConfig

API_BASE = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/"
WARNINGS = "W-C0033-001"
EARTHQUAKES = "E-A0015-001"
DATASET_NAMES = {
    WARNINGS: "天氣特報-各別縣市地區目前之天氣警特報情形",
    EARTHQUAKES: "顯著有感地震報告",
}
WARNINGS_PAGE = "https://www.cwa.gov.tw/V8/C/P/Warning/FIFOWS.html"
EARTHQUAKE_PAGE = "https://scweb.cwa.gov.tw/zh-tw/earthquake/data"
LICENSE_URL = "https://data.gov.tw/license"
TAIPEI = timezone(timedelta(hours=8))
_RETRYABLE_STATUS = frozenset({408, 425, 429})
_MAX_BYTES = 5_000_000

ApiKeyProvider = Callable[[], SecretStr | None]


class CwaOptions(BaseModel):
    """氣象署來源的設定；無效值在建立 Adapter 時拋出 ValueError。授權碼不得寫在這裡。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    county: str = Field(min_length=1)
    datasets: tuple[str, ...] = (WARNINGS, EARTHQUAKES)
    earthquake_limit: int = Field(default=20, ge=1, le=50)
    timeout: float = Field(default=15.0, gt=0, le=60)

    @field_validator("datasets")
    @classmethod
    def _known_datasets(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("datasets 至少要有一個")
        unknown = set(value) - set(DATASET_NAMES)
        if unknown:
            raise ValueError(f"不支援的資料集：{sorted(unknown)}")
        return value


def _settings_api_key() -> SecretStr | None:
    return get_settings().cwa_api_key


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _attribution(dataset: str, year: int) -> str:
    return (
        f"資料來源：中央氣象署（{year}），{dataset} {DATASET_NAMES[dataset]}。"
        f"依政府資料開放授權條款第 1 版（{LICENSE_URL}）使用。"
    )


@registry.register("cwa_open_data")
class CwaOpenDataAdapter:
    """氣象署開放資料 Adapter。"""

    code: str

    def __init__(
        self,
        config: SourceConfig,
        *,
        client: httpx2.AsyncClient | None = None,
        limiter: HostRateLimiter = shared_limiter,
        clock: Callable[[], datetime] = _utc_now,
        api_key: ApiKeyProvider = _settings_api_key,
    ) -> None:
        self.code = config.code
        self._options = CwaOptions.model_validate(dict(config.options))
        self._client = client
        self._limiter = limiter
        self._clock = clock
        self._api_key = api_key

    async def fetch_since(self, cursor: str | None) -> list[RawItem]:
        """取得目前的警特報與地震報告；`cursor` 不使用。"""
        key = self._api_key()
        if key is None or not key.get_secret_value().strip():
            raise AdapterError(f"來源 {self.code} 未設定 CWA_API_KEY")
        client = self._client or httpx2.AsyncClient()
        try:
            items: list[RawItem] = []
            fetched_at = self._clock()
            if WARNINGS in self._options.datasets:
                data = await self._get(
                    client, key, WARNINGS, {"locationName": self._options.county}
                )
                items.extend(self._warnings(data, fetched_at))
            if EARTHQUAKES in self._options.datasets:
                params = {"limit": str(self._options.earthquake_limit)}
                data = await self._get(client, key, EARTHQUAKES, params)
                items.extend(self._earthquakes(data, fetched_at))
            return items
        finally:
            if self._client is None:
                await client.aclose()

    def next_cursor(self, items: Sequence[RawItem], previous: str | None) -> str | None:
        """快照式資料不使用 cursor，原樣回傳。"""
        return previous

    # --- HTTP ---------------------------------------------------------------------------

    async def _get(
        self, client: httpx2.AsyncClient, key: SecretStr, dataset: str, params: dict[str, str]
    ) -> dict[str, Any]:
        url = API_BASE + dataset
        await self._limiter.wait(httpx2.URL(url).host, MIN_REQUEST_INTERVAL)
        try:
            response = await client.get(
                url,
                params={**params, "format": "JSON"},
                headers={
                    "Authorization": key.get_secret_value(),
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                },
                timeout=self._options.timeout,
            )
        except httpx2.TransportError as exc:
            raise SourceUnavailableError(
                f"來源 {self.code} 的 {dataset} 連線失敗（{type(exc).__name__}）"
            ) from None
        except httpx2.HTTPError as exc:
            raise AdapterError(
                f"來源 {self.code} 的 {dataset} 請求失敗（{type(exc).__name__}）"
            ) from None
        status = response.status_code
        if status >= 500 or status in _RETRYABLE_STATUS:
            raise SourceUnavailableError(f"來源 {self.code} 的 {dataset} 回應 HTTP {status}")
        if status in (401, 403):
            raise AdapterError(f"來源 {self.code} 的授權碼無效或權限不足（HTTP {status}）")
        if status >= 400:
            raise AdapterError(f"來源 {self.code} 的 {dataset} 回應 HTTP {status}")
        if len(response.content) > _MAX_BYTES:
            raise AdapterError(f"來源 {self.code} 的 {dataset} 回應過大")
        try:
            data = response.json()
        except ValueError:
            raise LayoutChangedError(f"來源 {self.code} 的 {dataset} 不是 JSON") from None
        if not isinstance(data, dict) or str(data.get("success")).lower() != "true":
            raise AdapterError(f"來源 {self.code} 的 {dataset} 回應 success 不是 true")
        records = data.get("records")
        if not isinstance(records, dict):
            raise LayoutChangedError(f"來源 {self.code} 的 {dataset} 缺少 records")
        return records

    # --- 天氣警特報 ---------------------------------------------------------------------

    def _warnings(self, records: dict[str, Any], fetched_at: datetime) -> list[RawItem]:
        locations = records.get("location")
        if not isinstance(locations, list):
            raise LayoutChangedError(f"來源 {self.code} 的 {WARNINGS} 缺少 location")
        county = self._options.county
        items: list[RawItem] = []
        for location in locations:
            if not isinstance(location, dict) or location.get("locationName") != county:
                continue
            hazards = (location.get("hazardConditions") or {}).get("hazards") or []
            for hazard in hazards:
                items.append(self._warning_item(hazard, fetched_at))
        return items

    def _warning_item(self, hazard: Any, fetched_at: datetime) -> RawItem:
        try:
            info, valid = hazard["info"], hazard["validTime"]
            name = f"{info['phenomena']}{info['significance']}"
            start = _taipei(valid["startTime"])
            end = _taipei(valid["endTime"])
        except (KeyError, TypeError, ValueError):
            raise LayoutChangedError(f"來源 {self.code} 的 {WARNINGS} 警特報欄位缺漏") from None
        county = self._options.county
        content = "\n".join(
            [
                f"中央氣象署發布{county}{name}。",
                f"有效時間：{start:%Y-%m-%d %H:%M} 至 {end:%Y-%m-%d %H:%M}（臺灣時間）。",
                "",
                _attribution(WARNINGS, start.year),
            ]
        )
        return RawItem(
            source_code=self.code,
            external_id=f"{WARNINGS}:{county}:{name}:{start.isoformat()}",
            url=WARNINGS_PAGE,
            title=f"{county}{name}",
            content=content,
            published_at=start,
            fetched_at=fetched_at,
        )

    # --- 地震報告 -----------------------------------------------------------------------

    def _earthquakes(self, records: dict[str, Any], fetched_at: datetime) -> list[RawItem]:
        quakes = records.get("Earthquake")
        if not isinstance(quakes, list):
            raise LayoutChangedError(f"來源 {self.code} 的 {EARTHQUAKES} 缺少 Earthquake")
        items: list[RawItem] = []
        for quake in quakes:
            intensity = self._county_intensity(quake)
            if intensity is not None:
                items.append(self._earthquake_item(quake, intensity, fetched_at))
        return items

    def _county_intensity(self, quake: Any) -> str | None:
        """指定縣市的最大震度：優先取該縣市自己的條目，其次取分組條目；沒有則為 None。"""
        try:
            areas = quake["Intensity"]["ShakingArea"]
        except (KeyError, TypeError):
            raise LayoutChangedError(f"來源 {self.code} 的 {EARTHQUAKES} 缺少震度分布") from None
        county = self._options.county
        grouped: str | None = None
        for area in areas if isinstance(areas, list) else []:
            names = str(area.get("CountyName", "")).split("、")
            level = str(area.get("AreaIntensity", "")).strip() or "未知"
            if names == [county]:
                return level
            if county in names and grouped is None:
                grouped = level
        return grouped

    def _earthquake_item(self, quake: Any, intensity: str, fetched_at: datetime) -> RawItem:
        try:
            number = int(quake["EarthquakeNo"])
            info = quake["EarthquakeInfo"]
            origin = datetime.fromisoformat(info["OriginTime"])
            report = str(quake["ReportContent"]).strip()
            epicenter = str(info["Epicenter"]["Location"]).strip()
            magnitude = info["EarthquakeMagnitude"]
            depth = float(info["FocalDepth"])
        except (KeyError, TypeError, ValueError):
            raise LayoutChangedError(f"來源 {self.code} 的 {EARTHQUAKES} 地震欄位缺漏") from None
        if origin.tzinfo is None:
            origin = origin.replace(tzinfo=TAIPEI)
        web = quake.get("Web")
        url = web if isinstance(web, str) and web.startswith(("http://", "https://")) else None
        content = "\n".join(
            [
                report,
                f"{self._options.county}最大震度：{intensity}。",
                f"震央：{' '.join(epicenter.split())}；"
                f"{magnitude.get('MagnitudeType', '規模')} {magnitude.get('MagnitudeValue')}；"
                f"深度 {depth:g} 公里。",
                "",
                _attribution(EARTHQUAKES, origin.year),
            ]
        )
        return RawItem(
            source_code=self.code,
            external_id=f"{EARTHQUAKES}:{number}",
            url=url or EARTHQUAKE_PAGE,
            title=f"顯著有感地震報告 第 {number} 號",
            content=content,
            published_at=origin,
            fetched_at=fetched_at,
        )


def _taipei(value: str) -> datetime:
    """警特報的時間是沒有時區的臺灣時間（例如 2026-10-17 08:30:00）。"""
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=TAIPEI)
