"""匿名回報入口（P5-T13，ADR-0015 第 8 點）：`POST /api/v1/public/reports`。

公開 API 中唯一的寫入端點，免登入。投稿只寫進 `anonymous_reports` 收件匣，之後由
`anonymous_report` Adapter 以另一個不可信來源拉進既有管線；不會因為投稿而直接公開，原文永不公開。

- 不保存 IP 與裝置資訊：資料表只有內容、選填地點與收到時間；稽核紀錄只記字數與有無地點；
  日誌只有 request 中介層的方法、路徑與狀態碼。
- 速率限制只用短期雜湊：每個用戶端 10 分鐘 3 則、全站 10 分鐘 30 則（`RateLimiter`，
  加鹽雜湊、只在記憶體、時間窗結束即清空）。在解析內容之前計數，格式錯誤的請求也算一次。
- 長度與格式限制：只收 `application/json`，請求本文最多 8 KiB；內容 10–2000 字、地點最多
  100 字；只接受 `content`、`location` 兩個欄位（不收聯絡方式）；拒絕控制字元與雙向文字控制字元。
- 回應 202 與固定內容，不回顯投稿，也不回傳收件匣 id；錯誤回應同樣不含輸入（`no-store`）。
"""

from __future__ import annotations

import re
import time
import unicodedata
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.api.errors import ApiError, ErrorCode, error_response
from app.api.responses import envelope
from app.api.v1.public.route import PublicRoute, RateLimiter, client_host
from app.core.metrics import count_rate_limited
from app.core.security import ANONYMOUS
from app.models.anonymous_report import (
    CONTENT_MAX_CHARS,
    CONTENT_MIN_CHARS,
    LOCATION_MAX_CHARS,
    AnonymousReport,
)
from app.services.audit import record_audit

router = APIRouter(tags=["public"], route_class=PublicRoute)

CLIENT_LIMIT = 3
"""每個用戶端每個時間窗最多幾則投稿。"""
CLIENT_WINDOW_SECONDS = 600
OVERALL_LIMIT = 30
"""全站每個時間窗最多幾則投稿（擋住換位址的洪水投稿）。"""
OVERALL_WINDOW_SECONDS = 600
MAX_BODY_BYTES = 8192
MIN_CONTENT_CHARS = CONTENT_MIN_CHARS
MAX_CONTENT_CHARS = CONTENT_MAX_CHARS

# C0／C1 控制字元（換行與 tab 除外）與雙向文字控制字元
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f‎‏‪-‮⁦-⁩]")


class ReportRateLimiter:
    """匿名回報的限速：先看單一用戶端，再看全站。"""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.per_client = RateLimiter(CLIENT_LIMIT, CLIENT_WINDOW_SECONDS, clock=clock)
        self.overall = RateLimiter(
            OVERALL_LIMIT, OVERALL_WINDOW_SECONDS, clock=clock, max_clients=1
        )

    def hit(self, client: str) -> int | None:
        """記錄一次投稿；超過任一上限時回傳建議等待秒數。"""
        retry_after = self.per_client.hit(client)
        if retry_after is not None:
            return retry_after
        return self.overall.hit("all")


def _clean(value: str, *, multiline: bool) -> str:
    text = unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n")).strip()
    if _CONTROL.search(text) or (not multiline and "\n" in text):
        raise ValueError("含有不允許的控制字元")
    return text


class ReportIn(BaseModel):
    """投稿內容；不接受任何其他欄位。"""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    content: str
    location: str | None = None

    @field_validator("content")
    @classmethod
    def _content(cls, value: str) -> str:
        text = _clean(value, multiline=True)
        if not MIN_CONTENT_CHARS <= len(text) <= MAX_CONTENT_CHARS:
            raise ValueError(f"內容需為 {MIN_CONTENT_CHARS}–{MAX_CONTENT_CHARS} 字")
        return text

    @field_validator("location")
    @classmethod
    def _location(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = _clean(value, multiline=False)
        if len(text) > LOCATION_MAX_CHARS:
            raise ValueError(f"地點最多 {LOCATION_MAX_CHARS} 字")
        return text or None


def _invalid(message: str, details: list[dict[str, str]] | None = None) -> ApiError:
    return ApiError(ErrorCode.VALIDATION_ERROR, message, details=details)


async def _read_body(request: Request) -> bytes:
    """讀取請求本文，超過上限就停止讀取。"""
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        raise _invalid("請以 JSON 送出")
    declared = request.headers.get("content-length")
    if declared is not None and (not declared.isdigit() or int(declared) > MAX_BODY_BYTES):
        raise _invalid("內容太長")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BODY_BYTES:
            raise _invalid("內容太長")
    return bytes(body)


def _parse(body: bytes) -> ReportIn:
    try:
        return ReportIn.model_validate_json(body)
    except ValidationError as exc:
        # 只回傳欄位與說明，不含 pydantic 附帶的 input（投稿原文）
        details = [
            {
                "field": ".".join(str(part) for part in err.get("loc", ())),
                "message": str(err.get("msg")),
            }
            for err in exc.errors(include_input=False, include_url=False)
        ]
        raise _invalid("請求參數不正確", details) from None


@router.post("/reports", status_code=202)
async def submit_report(
    request: Request, session: Annotated[AsyncSession, Depends(get_db_session)]
) -> JSONResponse:
    """收下一則匿名回報；只寫進收件匣，不直接進入分析或公開。"""
    limiter: ReportRateLimiter = request.app.state.report_rate_limiter
    retry_after = limiter.hit(client_host(request))
    if retry_after is not None:
        count_rate_limited("report")
        limited = error_response(ApiError(ErrorCode.RATE_LIMITED, "回報太頻繁，請稍後再試"))
        limited.headers["Retry-After"] = str(retry_after)
        return limited

    report = _parse(await _read_body(request))
    row = AnonymousReport(content=report.content, location=report.location)
    session.add(row)
    await session.flush()
    await record_audit(
        session,
        ANONYMOUS,
        "anonymous_report.submit",
        target=f"anonymous_reports/{row.id}",
        metadata={
            "content_chars": len(report.content),
            "has_location": report.location is not None,
        },
    )
    await session.commit()
    return JSONResponse(envelope({"status": "received"}), status_code=202)
