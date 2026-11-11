"""P8-T16：後台匿名回報收件匣（`GET /api/v1/admin/anonymous-reports`）。

- 需要登入（只在 `/api/v1/admin/*`）；原文只在管理端出現。
- 不回傳收據碼或它的雜湊；補件依同一張收據分組到原投稿之下。
- 每則投稿顯示收到時間、原投稿或補件、擷取與分析狀態、所屬事件；沒有成立事件的投稿也看得到。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session, get_principal
from app.core.security import Principal, Role
from app.main import create_app
from app.models import Event, EventEvidence, Source, SourceItem
from app.models.anonymous_report import AnonymousReport, ReportReceipt
from app.services.report_receipts import SOURCE_CODE, external_id

pytestmark = pytest.mark.anyio

INBOX = "/api/v1/admin/anonymous-reports"
ADMIN = Principal(kind="admin", subject="u-1", roles=frozenset({Role.ADMIN}))
ANONYMOUS = Principal(kind="anonymous", subject="", roles=frozenset())
T0 = datetime(2026, 11, 1, 8, 0, tzinfo=UTC)
CODE = "ABCD-EFGH-IJKL-MNOP-QRST-UVWX"
CODE_HASH = hashlib.sha256(CODE.encode()).hexdigest()


@pytest.fixture()
async def app(session: AsyncSession) -> AsyncIterator[FastAPI]:
    app = create_app()

    async def test_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_db_session] = test_session
    app.dependency_overrides[get_principal] = lambda: ADMIN
    yield app


@pytest.fixture()
async def client(app: FastAPI) -> AsyncIterator[httpx2.AsyncClient]:
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="https://test") as client:
        yield client


async def add_report(
    session: AsyncSession,
    content: str,
    *,
    at: datetime,
    location: str | None = None,
    code_hash: str | None = None,
    supplement: bool = False,
) -> AnonymousReport:
    report = AnonymousReport(content=content, location=location, submitted_at=at)
    session.add(report)
    await session.flush()
    if code_hash is not None:
        session.add(
            ReportReceipt(report_id=report.id, code_hash=code_hash, is_supplement=supplement)
        )
        await session.flush()
    return report


async def ingest(
    session: AsyncSession, source: Source, report: AnonymousReport, status: str = "done"
) -> SourceItem:
    item = SourceItem(
        source_id=source.id,
        external_id=external_id(report.id),
        url=f"http://127.0.0.1:8080/report?id={report.id}",
        published_at=report.submitted_at,
        analysis_status=status,
    )
    session.add(item)
    await session.flush()
    return item


async def anon_source(session: AsyncSession) -> Source:
    source = Source(code=SOURCE_CODE, name="匿名回報", type="report", access_policy="approved")
    session.add(source)
    await session.flush()
    return source


async def test_requires_login(app: FastAPI, client: httpx2.AsyncClient) -> None:
    app.dependency_overrides[get_principal] = lambda: ANONYMOUS
    res = await client.get(INBOX)
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "UNAUTHORIZED"


async def test_groups_supplements_under_original_without_receipt_secrets(
    client: httpx2.AsyncClient, session: AsyncSession
) -> None:
    source = await anon_source(session)
    original = await add_report(
        session,
        "工程五館三樓走廊燈管一直閃爍，晚上很暗",
        at=T0,
        location="工程五館",
        code_hash=CODE_HASH,
    )
    other = await add_report(session, "宿舍區飲水機漏水，地板很滑", at=T0 + timedelta(hours=1))
    extra = await add_report(
        session,
        "補充：今天早上還是一樣，緊急照明也沒亮",
        at=T0 + timedelta(hours=2),
        code_hash=CODE_HASH,
        supplement=True,
    )
    item = await ingest(session, source, original)
    await ingest(session, source, extra, status="queued")
    event = Event(title="工程五館走廊燈管閃爍", type="facility", status="unverified")
    session.add(event)
    await session.flush()
    session.add(EventEvidence(event_id=event.id, source_item_id=item.id, support_type="supports"))
    await session.commit()

    res = await client.get(INBOX)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["error"] is None
    raw = json.dumps(body, ensure_ascii=False)
    assert CODE not in raw
    assert CODE_HASH not in raw
    assert "code_hash" not in raw and "receipt_code" not in raw

    groups = body["data"]
    # 新到舊（以原投稿的收到時間）：宿舍那則較晚收到
    assert [g["id"] for g in groups] == [other.id, original.id]
    lone, grouped = groups
    assert [r["id"] for r in lone["reports"]] == [other.id]
    assert lone["reports"][0]["kind"] == "original"
    # 沒被擷取、沒有成立事件的投稿也看得到
    assert lone["reports"][0]["source_item_id"] is None
    assert lone["reports"][0]["ingestion_status"] == "not_ingested"
    assert lone["reports"][0]["analysis_status"] is None
    assert lone["reports"][0]["events"] == []
    assert lone["events"] == []

    assert [(r["id"], r["kind"]) for r in grouped["reports"]] == [
        (original.id, "original"),
        (extra.id, "supplement"),
    ]
    first, second = grouped["reports"]
    assert first["content"] == "工程五館三樓走廊燈管一直閃爍，晚上很暗"
    assert first["location"] == "工程五館"
    assert datetime.fromisoformat(first["submitted_at"]) == T0
    assert first["ingestion_status"] == "ingested"
    assert first["analysis_status"] == "done"
    assert first["source_item_id"] == str(item.id)
    assert first["events"] == [
        {"id": str(event.id), "title": "工程五館走廊燈管閃爍", "status": "unverified"}
    ]
    assert second["analysis_status"] == "queued"
    assert second["events"] == []
    assert grouped["events"] == first["events"]
    assert datetime.fromisoformat(grouped["latest_submitted_at"]) == T0 + timedelta(hours=2)


async def test_paginates_by_group(client: httpx2.AsyncClient, session: AsyncSession) -> None:
    for i in range(3):
        code_hash = hashlib.sha256(f"code-{i}".encode()).hexdigest()
        await add_report(
            session, f"第{i}則投稿，內容足夠長", at=T0 + timedelta(hours=i), code_hash=code_hash
        )
        await add_report(
            session,
            f"第{i}則的補件，內容足夠長",
            at=T0 + timedelta(hours=i, minutes=5),
            code_hash=code_hash,
            supplement=True,
        )
    await session.commit()

    contents: list[list[str]] = []
    cursor: str | None = None
    while True:
        params: dict[str, Any] = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        body = (await client.get(INBOX, params=params)).json()
        contents += [[r["content"][:3] for r in g["reports"]] for g in body["data"]]
        cursor = body["meta"]["next_cursor"]
        if not cursor:
            break
    assert contents == [["第2則", "第2則"], ["第1則", "第1則"], ["第0則", "第0則"]]


async def test_rejects_bad_cursor(client: httpx2.AsyncClient) -> None:
    res = await client.get(INBOX, params={"cursor": "not-a-cursor"})
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"
