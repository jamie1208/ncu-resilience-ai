"""公開 API 共用的請求處理（P5-T11）：rate limit 與快取標頭。

所有 `/api/v1/public/*` 路由都以 `PublicRoute` 建立：

- rate limit：每個用戶端在固定時間窗內的請求數上限，超過回 `RATE_LIMITED`（429）與
  `Retry-After`。用戶端以「行程啟動時產生的隨機鹽 + 連線位址」的 SHA-256 識別，只存在記憶體，
  時間窗結束即清空，不落地、不寫日誌，記憶體中也沒有原始位址。
- 成功回應：`Cache-Control: public, max-age=30` 與弱 ETag（只依 data 與分頁 meta 計算，
  不含每次不同的 request_id、timestamp）；`If-None-Match` 相符時回 304。
- 錯誤回應（含 429、404、422）：`Cache-Control: no-store`。
"""

from __future__ import annotations

import hashlib
import json
import math
import secrets
import time
from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.errors import ApiError, ErrorCode, error_response

RATE_LIMIT = 60
"""每個用戶端每個時間窗的請求數上限。"""
RATE_WINDOW_SECONDS = 60
MAX_TRACKED_CLIENTS = 10_000
"""同一時間窗最多追蹤的用戶端數；超過後新的用戶端共用一個計數，避免記憶體被灌爆。"""
CACHE_CONTROL = "public, max-age=30"
NO_STORE = "no-store"
_VOLATILE_META = frozenset({"request_id", "timestamp"})


class RateLimiter:
    """固定時間窗計數器（行程內）；`hit()` 超過上限時回傳建議等待秒數。"""

    def __init__(
        self,
        limit: int = RATE_LIMIT,
        window_seconds: int = RATE_WINDOW_SECONDS,
        *,
        clock: Callable[[], float] = time.monotonic,
        max_clients: int = MAX_TRACKED_CLIENTS,
    ) -> None:
        self._limit = limit
        self._window_seconds = window_seconds
        self._clock = clock
        self._max_clients = max_clients
        self._salt = secrets.token_bytes(16)
        self._window: int | None = None
        self._counts: dict[str, int] = {}

    def _key(self, client: str) -> str:
        return hashlib.sha256(self._salt + client.encode()).hexdigest()[:32]

    def hit(self, client: str) -> int | None:
        """記錄一次請求；未超過上限回傳 None，超過時回傳距離時間窗結束的秒數（至少 1）。"""
        now = self._clock()
        window = int(now // self._window_seconds)
        if window != self._window:
            self._window = window
            self._counts = {}
        key = self._key(client)
        if key not in self._counts and len(self._counts) >= self._max_clients:
            key = "overflow"
        count = self._counts.get(key, 0) + 1
        self._counts[key] = count
        if count <= self._limit:
            return None
        return max(1, math.ceil((window + 1) * self._window_seconds - now))


def _client(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def etag_for(body: bytes) -> str:
    """依回應的 data 與分頁 meta 計算弱 ETag。"""
    payload = json.loads(body)
    stable = {
        "data": payload.get("data"),
        "meta": {k: v for k, v in (payload.get("meta") or {}).items() if k not in _VOLATILE_META},
    }
    raw = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f'W/"{hashlib.sha256(raw.encode()).hexdigest()[:32]}"'


def _matches(header: str | None, etag: str) -> bool:
    if not header:
        return False
    return header.strip() == "*" or etag in (tag.strip() for tag in header.split(","))


class PublicRoute(APIRoute):
    """公開路由：先檢查 rate limit，再依結果加上快取標頭。"""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        """包裝 FastAPI 原本的處理函式。"""
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            limiter: RateLimiter = request.app.state.public_rate_limiter
            retry_after = limiter.hit(_client(request))
            if retry_after is not None:
                limited = error_response(ApiError(ErrorCode.RATE_LIMITED, "請求太頻繁，請稍後再試"))
                limited.headers["Retry-After"] = str(retry_after)
                limited.headers["Cache-Control"] = NO_STORE
                return limited
            try:
                response = await original(request)
            except (ApiError, RequestValidationError, StarletteHTTPException) as exc:
                handle = request.app.exception_handlers[type(exc)]
                failed: Response = await handle(request, exc)
                failed.headers["Cache-Control"] = NO_STORE
                return failed
            if response.status_code != 200:
                response.headers["Cache-Control"] = NO_STORE
                return response
            etag = etag_for(bytes(response.body))
            headers = {"ETag": etag, "Cache-Control": CACHE_CONTROL}
            if _matches(request.headers.get("if-none-match"), etag):
                return Response(status_code=304, headers=headers)
            response.headers.update(headers)
            return response

        return handler
