"""API 錯誤碼與例外處理（規格 §10.1）。

錯誤一律以統一格式回應，不回顯使用者輸入（避免把投稿內容或個資反射回去）。
"""

from __future__ import annotations

import logging
from enum import StrEnum

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.responses import error_envelope
from app.core.security import ANONYMOUS, PermissionDeniedError

logger = logging.getLogger("app.api")


class ErrorCode(StrEnum):
    """錯誤碼；前七個為規格 §10.1 的最低要求。"""

    VALIDATION_ERROR = "VALIDATION_ERROR"
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    RATE_LIMITED = "RATE_LIMITED"
    CONFLICT = "CONFLICT"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


STATUS_CODES: dict[ErrorCode, int] = {
    ErrorCode.VALIDATION_ERROR: 422,
    ErrorCode.UNAUTHORIZED: 401,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.METHOD_NOT_ALLOWED: 405,
    ErrorCode.CONFLICT: 409,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.INTERNAL_ERROR: 500,
    ErrorCode.MODEL_UNAVAILABLE: 503,
}


class ApiError(Exception):
    """以錯誤碼回應的 API 例外；`message` 不得包含使用者輸入或個資。"""

    def __init__(
        self, code: ErrorCode, message: str, *, details: list[dict[str, str]] | None = None
    ) -> None:
        self.code = code
        self.message = message
        self.details = details
        super().__init__(message)

    @property
    def status_code(self) -> int:
        """對應的 HTTP 狀態碼。"""
        return STATUS_CODES[self.code]


def error_response(error: ApiError) -> JSONResponse:
    """把 ApiError 轉成統一格式的 JSON 回應。"""
    return JSONResponse(
        error_envelope(error.code.value, error.message, error.details),
        status_code=error.status_code,
    )


async def _api_error(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, ApiError):
        raise exc
    return error_response(exc)


async def _validation_error(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, RequestValidationError):
        raise exc
    # 只回傳欄位位置與錯誤說明，不含 pydantic 附帶的 input（可能是使用者輸入）
    details = [
        {
            "field": ".".join(str(part) for part in err.get("loc", ())),
            "message": str(err.get("msg")),
        }
        for err in exc.errors()
    ]
    return error_response(ApiError(ErrorCode.VALIDATION_ERROR, "請求參數不正確", details=details))


async def _permission_denied(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, PermissionDeniedError):
        raise exc
    if exc.actor == ANONYMOUS.actor:
        return error_response(ApiError(ErrorCode.UNAUTHORIZED, "需要登入"))
    return error_response(ApiError(ErrorCode.FORBIDDEN, "沒有執行此操作的權限"))


async def _http_error(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, StarletteHTTPException):
        raise exc
    by_status = {
        404: (ErrorCode.NOT_FOUND, "找不到資源"),
        405: (ErrorCode.METHOD_NOT_ALLOWED, "不支援此 HTTP 方法"),
        401: (ErrorCode.UNAUTHORIZED, "需要登入"),
        403: (ErrorCode.FORBIDDEN, "沒有執行此操作的權限"),
    }
    code, message = by_status.get(exc.status_code, (ErrorCode.VALIDATION_ERROR, "請求不正確"))
    return JSONResponse(error_envelope(code.value, message), status_code=exc.status_code)


async def _unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    # 只記例外類型：訊息可能含投稿原文或個資；回應也不帶任何細節
    logger.error("unhandled_error", extra={"fields": {"error_type": type(exc).__name__}})
    return error_response(ApiError(ErrorCode.INTERNAL_ERROR, "伺服器內部錯誤"))


def register_error_handlers(app: FastAPI) -> None:
    """註冊例外處理，讓所有錯誤都以 `{data, meta, error}` 回應。"""
    app.add_exception_handler(ApiError, _api_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(PermissionDeniedError, _permission_denied)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(Exception, _unexpected_error)
