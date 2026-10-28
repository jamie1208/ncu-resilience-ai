"""公開 API（/api/v1/public/*，P5-T11，ADR-0015）：免登入；除了匿名回報（P5-T13）都只有 GET。

只讀取公開規則（P5-T10）判定可公開的版本；每個路由都經 `PublicRoute` 的 rate limit 與
快取標頭。端點、濫用方式與防護見 `docs/architecture/threat-model-public.md`。
"""

from fastapi import APIRouter

from app.api.v1.public import content, reports
from app.api.v1.public.route import PublicRoute, RateLimiter

router = APIRouter(prefix="/public", route_class=PublicRoute)
router.include_router(content.router)
router.include_router(reports.router)

__all__ = ["RateLimiter", "router"]
