"""FastAPI 應用程式進入點：`uvicorn app.main:app`。"""

from __future__ import annotations

import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response

from app.api.errors import register_error_handlers
from app.api.v1 import admin, auth, health, public
from app.api.v1.public.route import RateLimiter
from app.core.config import get_settings
from app.core.logging import configure_logging, request_id_var

REQUEST_ID_HEADER = "X-Request-ID"
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")

logger = logging.getLogger("app.request")


def _resolve_request_id(incoming: str | None) -> str:
    """沿用格式安全的上游 request_id，否則產生新的 UUID4（避免日誌注入）。"""
    if incoming and _VALID_REQUEST_ID.fullmatch(incoming):
        return incoming
    return str(uuid.uuid4())


def create_app() -> FastAPI:
    """建立並設定 FastAPI 應用程式。"""
    settings = get_settings()
    configure_logging(settings.log_level)

    # API 文件預設關閉：免登入的只有 health、登入與公開唯讀 API（P3-T01、P5-T11）
    docs = settings.api_docs_enabled
    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
    )

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        rid = _resolve_request_id(request.headers.get(REQUEST_ID_HEADER))
        token = request_id_var.set(rid)
        start = time.perf_counter()
        try:
            response = await call_next(request)
            response.headers[REQUEST_ID_HEADER] = rid
            logger.info(
                "request",
                extra={
                    "fields": {
                        "method": request.method,
                        "path": request.url.path,
                        "status": response.status_code,
                        "duration_ms": round((time.perf_counter() - start) * 1000, 1),
                    }
                },
            )
            return response
        finally:
            request_id_var.reset(token)

    register_error_handlers(app)
    app.include_router(health.router, prefix=settings.api_prefix)
    app.include_router(auth.router, prefix=settings.api_prefix)
    app.include_router(admin.router, prefix=settings.api_prefix)
    app.include_router(public.router, prefix=settings.api_prefix)
    app.state.public_rate_limiter = RateLimiter()
    return app


app = create_app()
