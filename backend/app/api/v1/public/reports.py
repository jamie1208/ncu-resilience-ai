"""匿名回報入口（P5-T13，ADR-0015 第 8 點）：`POST /api/v1/public/reports`。

公開 API 中唯一的寫入端點，免登入。投稿只寫進 `anonymous_reports` 收件匣，之後由
`anonymous_report` Adapter 以另一個不可信來源拉進既有管線；不會因為投稿而直接公開，原文永不公開。

- 不保存 IP 與裝置資訊：資料表只有內容、選填地點與收到時間；稽核紀錄只記字數與有無地點；
  日誌只有 request 中介層的方法、路徑與狀態碼。
- 速率限制只用短期雜湊：每個用戶端 10 分鐘 3 則、全站 10 分鐘 30 則（`RateLimiter`，
  加鹽雜湊、只在記憶體、時間窗結束即清空）。在解析內容之前計數，格式錯誤的請求也算一次。
- 長度與格式限制：只收 `application/json`，請求本文最多 8 KiB；內容 10–2000 字、地點最多
  100 字；只接受 `content`、`location` 兩個欄位（不收聯絡方式）；拒絕控制字元與雙向文字控制字元。
- 回應 202，不回顯投稿，也不回傳收件匣 id；錯誤回應同樣不含輸入（`no-store`）。

收據碼與補件（P8-T08，v1.0 §2、D11）：
- 送出時回傳一次高熵收據碼（15 bytes 隨機、base32、`XXXX-XXXX-XXXX-XXXX-XXXX-XXXX`），
  資料庫只存正規化後的 SHA-256（`report_receipts`），明文不落地、不寫稽核與日誌。
- `GET /reports/status`：收據碼放在 `X-Receipt-Code` 標頭（request 日誌會記錄路徑，所以不放網址），
  只回 received／processing／handled，不回投稿內容；回應 `no-store`、沒有 ETag。
  錯的、格式不對、缺少與不存在的收據碼一律回相同的 404。查詢有自己的限速（暴力猜碼）。
- `POST /reports/supplements`：補件與投稿共用限速、同樣的長度與格式限制；寫進同一個收件匣，
  由既有 Adapter 拉進管線（以 `<untrusted_source>` 包裹），原文永不公開。每張收據最多補件
  `MAX_SUPPLEMENTS` 次。
"""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
import time
import unicodedata
from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.api.errors import ApiError, ErrorCode, error_response
from app.api.responses import envelope
from app.api.v1.public.route import NO_STORE, PublicRoute, RateLimiter, client_host
from app.core.metrics import count_rate_limited
from app.core.security import ANONYMOUS
from app.domain.states import EventStatus
from app.models.anonymous_report import (
    CONTENT_MAX_CHARS,
    CONTENT_MIN_CHARS,
    LOCATION_MAX_CHARS,
    AnonymousReport,
    ReportReceipt,
)
from app.models.event import Event, EventEvidence
from app.models.source import AnalysisStatus
from app.services.audit import record_audit
from app.services.report_receipts import report_items

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

RECEIPT_BYTES = 15
"""收據碼的隨機位元組數（120 位元）。"""
RECEIPT_CHARS = RECEIPT_BYTES * 8 // 5
RECEIPT_GROUP = 4
RECEIPT_HEADER = "X-Receipt-Code"
MAX_RECEIPT_INPUT = 64
"""收據碼輸入（含空白與連字號）的最大長度；超過直接視為不存在。"""
MAX_SUPPLEMENTS = 5
"""每張收據最多補件幾次。"""
LOOKUP_CLIENT_LIMIT = 20
"""每個用戶端每個時間窗最多查詢幾次（含錯的收據碼；使用者 2026-11-10 決定 10 分鐘 20 次）。"""
LOOKUP_WINDOW_SECONDS = 600
LOOKUP_OVERALL_LIMIT = 600
CLOSED_EVENT_STATUSES = frozenset(
    {EventStatus.RESOLVED.value, EventStatus.ARCHIVED.value, EventStatus.FALSE.value}
)
FINISHED_ANALYSIS = frozenset(
    {AnalysisStatus.DONE.value, AnalysisStatus.SKIPPED.value, AnalysisStatus.FAILED.value}
)
_RECEIPT = re.compile(rf"^[A-Z2-7]{{{RECEIPT_CHARS}}}$")
_NOT_FOUND = "找不到這張收據碼"

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


class LookupRateLimiter:
    """收據碼查詢的限速：先看單一用戶端，再看全站。"""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.per_client = RateLimiter(LOOKUP_CLIENT_LIMIT, LOOKUP_WINDOW_SECONDS, clock=clock)
        self.overall = RateLimiter(
            LOOKUP_OVERALL_LIMIT, LOOKUP_WINDOW_SECONDS, clock=clock, max_clients=1
        )

    def hit(self, client: str) -> int | None:
        """記錄一次查詢；超過任一上限時回傳建議等待秒數。"""
        retry_after = self.per_client.hit(client)
        if retry_after is not None:
            return retry_after
        return self.overall.hit("all")


def _digest(normalized: str) -> str:
    return hashlib.sha256(normalized.encode()).hexdigest()


def new_receipt() -> tuple[str, str]:
    """產生一張收據碼；回傳（分組顯示的收據碼, 雜湊）。"""
    raw = base64.b32encode(secrets.token_bytes(RECEIPT_BYTES)).decode()
    code = "-".join(raw[i : i + RECEIPT_GROUP] for i in range(0, len(raw), RECEIPT_GROUP))
    return code, _digest(raw)


def receipt_hash(code: str) -> str | None:
    """正規化（去空白與連字號、轉大寫）後的 SHA-256；格式不對回傳 None。"""
    if len(code) > MAX_RECEIPT_INPUT:
        return None
    normalized = re.sub(r"[\s-]", "", code).upper()
    if not _RECEIPT.match(normalized):
        return None
    return _digest(normalized)


def _clean(value: str, *, multiline: bool) -> str:
    text = unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n")).strip()
    if _CONTROL.search(text) or (not multiline and "\n" in text):
        raise ValueError("含有不允許的控制字元")
    return text


def _content_text(value: str) -> str:
    text = _clean(value, multiline=True)
    if not MIN_CONTENT_CHARS <= len(text) <= MAX_CONTENT_CHARS:
        raise ValueError(f"內容需為 {MIN_CONTENT_CHARS}–{MAX_CONTENT_CHARS} 字")
    return text


def _location_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = _clean(value, multiline=False)
    if len(text) > LOCATION_MAX_CHARS:
        raise ValueError(f"地點最多 {LOCATION_MAX_CHARS} 字")
    return text or None


class ReportIn(BaseModel):
    """投稿內容；不接受任何其他欄位。"""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    content: str
    location: str | None = None

    @field_validator("content")
    @classmethod
    def _content(cls, value: str) -> str:
        return _content_text(value)

    @field_validator("location")
    @classmethod
    def _location(cls, value: str | None) -> str | None:
        return _location_text(value)


class SupplementIn(BaseModel):
    """補件：收據碼加上與投稿相同限制的內容與選填地點。"""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    receipt_code: str
    content: str
    location: str | None = None

    @field_validator("content")
    @classmethod
    def _content(cls, value: str) -> str:
        return _content_text(value)

    @field_validator("location")
    @classmethod
    def _location(cls, value: str | None) -> str | None:
        return _location_text(value)


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


def _parse[M: BaseModel](body: bytes, model: type[M]) -> M:
    try:
        return model.model_validate_json(body)
    except ValidationError as exc:
        # 只回傳欄位與說明，不含 pydantic 附帶的 input（投稿原文、收據碼）
        details = [
            {
                "field": ".".join(str(part) for part in err.get("loc", ())),
                "message": str(err.get("msg")),
            }
            for err in exc.errors(include_input=False, include_url=False)
        ]
        raise _invalid("請求參數不正確", details) from None


def _limited(retry_after: int, message: str) -> JSONResponse:
    limited = error_response(ApiError(ErrorCode.RATE_LIMITED, message))
    limited.headers["Retry-After"] = str(retry_after)
    return limited


def _hit_report_limit(request: Request) -> JSONResponse | None:
    limiter: ReportRateLimiter = request.app.state.report_rate_limiter
    retry_after = limiter.hit(client_host(request))
    if retry_after is None:
        return None
    count_rate_limited("report")
    return _limited(retry_after, "回報太頻繁，請稍後再試")


async def _receipt_reports(session: AsyncSession, code: str | None) -> list[ReportReceipt]:
    """收據碼對應的投稿；格式不對或不存在都回傳空串列（之後回相同的 404）。"""
    digest = receipt_hash(code or "")
    # 格式不對時仍查一次資料庫，讓回應時間與不存在的收據碼相近
    stmt = select(ReportReceipt).where(ReportReceipt.code_hash == (digest or "-" * 64))
    rows = list((await session.execute(stmt)).scalars())
    return rows if digest is not None else []


def _receipt_not_found() -> ApiError:
    return ApiError(ErrorCode.NOT_FOUND, _NOT_FOUND)


async def _report_status(session: AsyncSession, report_ids: list[int]) -> str:
    """依擷取、分析與事件狀態彙整出 received／processing／handled。

    - received：所有投稿都還沒被擷取進管線。
    - handled：所有投稿都分析完成（含判斷無關），而且相關事件都已結束（resolved、archived、
      false）或沒有成立事件。
    - 其他情況為 processing。
    """
    items = list((await report_items(session, report_ids)).values())
    if not items:
        return "received"
    if len(items) < len(report_ids) or any(s not in FINISHED_ANALYSIS for _, s in items):
        return "processing"
    open_events = await session.scalar(
        select(func.count())
        .select_from(EventEvidence)
        .join(Event, Event.id == EventEvidence.event_id)
        .where(
            EventEvidence.source_item_id.in_([item_id for item_id, _ in items]),
            Event.status.not_in(CLOSED_EVENT_STATUSES),
        )
    )
    return "processing" if open_events else "handled"


@router.post("/reports", status_code=202)
async def submit_report(
    request: Request, session: Annotated[AsyncSession, Depends(get_db_session)]
) -> JSONResponse:
    """收下一則匿名回報；只寫進收件匣，不直接進入分析或公開。回傳一次收據碼。"""
    limited = _hit_report_limit(request)
    if limited is not None:
        return limited

    report = _parse(await _read_body(request), ReportIn)
    row = AnonymousReport(content=report.content, location=report.location)
    session.add(row)
    await session.flush()
    code, digest = new_receipt()
    session.add(ReportReceipt(report_id=row.id, code_hash=digest, is_supplement=False))
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
    return JSONResponse(envelope({"status": "received", "receipt_code": code}), status_code=202)


async def report_status(
    request: Request, session: Annotated[AsyncSession, Depends(get_db_session)]
) -> JSONResponse:
    """以 `X-Receipt-Code` 標頭查詢處理狀態；只回 received／processing／handled。"""
    limiter: LookupRateLimiter = request.app.state.report_lookup_rate_limiter
    retry_after = limiter.hit(client_host(request))
    if retry_after is not None:
        count_rate_limited("report_lookup")
        return _limited(retry_after, "查詢太頻繁，請稍後再試")
    receipts = await _receipt_reports(session, request.headers.get(RECEIPT_HEADER))
    if not receipts:
        raise _receipt_not_found()
    status = await _report_status(session, [r.report_id for r in receipts])
    return JSONResponse(envelope({"status": status}))


@router.post("/reports/supplements", status_code=202)
async def submit_supplement(
    request: Request, session: Annotated[AsyncSession, Depends(get_db_session)]
) -> JSONResponse:
    """以收據碼補件；與投稿同樣限速與格式限制，寫進同一個收件匣。"""
    limited = _hit_report_limit(request)
    if limited is not None:
        return limited

    body = _parse(await _read_body(request), SupplementIn)
    receipts = await _receipt_reports(session, body.receipt_code)
    if not receipts:
        raise _receipt_not_found()
    if sum(r.is_supplement for r in receipts) >= MAX_SUPPLEMENTS:
        raise ApiError(ErrorCode.CONFLICT, f"每張收據最多補件 {MAX_SUPPLEMENTS} 次")
    row = AnonymousReport(content=body.content, location=body.location)
    session.add(row)
    await session.flush()
    session.add(
        ReportReceipt(report_id=row.id, code_hash=receipts[0].code_hash, is_supplement=True)
    )
    await record_audit(
        session,
        ANONYMOUS,
        "anonymous_report.supplement",
        target=f"anonymous_reports/{row.id}",
        metadata={
            "content_chars": len(body.content),
            "has_location": body.location is not None,
            "supplements": sum(r.is_supplement for r in receipts) + 1,
        },
    )
    await session.commit()
    return JSONResponse(envelope({"status": "received"}), status_code=202)


class ReceiptRoute(PublicRoute):
    """收據碼查詢：沿用公開路由的限速與錯誤處理，但回應一律 `no-store`、不給 ETag。

    同一個網址會因標頭中的收據碼而有不同內容，不能被瀏覽器或共用快取保存，也不能以
    `If-None-Match` 回 304。
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        """包裝公開路由的處理函式，覆寫快取標頭。"""
        inner = super().get_route_handler()

        async def handler(request: Request) -> Response:
            # 移除 If-None-Match，避免公開路由以 ETag 回 304
            request.scope["headers"] = [
                (k, v) for k, v in request.scope["headers"] if k.lower() != b"if-none-match"
            ]
            response = await inner(Request(request.scope, request.receive))
            response.headers["Cache-Control"] = NO_STORE
            if "etag" in response.headers:
                del response.headers["etag"]
            response.headers["Vary"] = RECEIPT_HEADER
            return response

        return handler


router.add_api_route(
    "/reports/status",
    report_status,
    methods=["GET"],
    route_class_override=ReceiptRoute,
)
