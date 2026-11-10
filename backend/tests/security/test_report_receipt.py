"""P8-T08：匿名回報收據碼與補件（v1.0 §2、D11）。

- 送出時回傳高熵收據碼，資料庫只存雜湊（沒有明文）。
- `GET /api/v1/public/reports/status`（收據碼放在 `X-Receipt-Code` 標頭，不進網址與日誌）
  只回 received／processing／handled；錯的、格式不對與不存在的收據碼回應完全相同；不回投稿內容。
- `POST /api/v1/public/reports/supplements` 補件：與投稿同樣限流與格式限制，寫進同一個收件匣，
  由既有 Adapter 以不可信來源拉進管線；補件原文不出現在公開端。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

import httpx2
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import get_principal
from app.api.v1.public.reports import (
    CLIENT_LIMIT,
    LOOKUP_CLIENT_LIMIT,
    MAX_SUPPLEMENTS,
    RECEIPT_HEADER,
)
from app.domain.states import EventStatus
from app.events.merge import merge_events
from app.models.anonymous_report import AnonymousReport, ReportReceipt
from app.models.audit import AuditLog
from app.models.event import Event, EventEvidence
from app.models.source import AnalysisStatus, SourceItem
from app.pipeline.analyze_item import Outcome, analyze_item
from app.publishing.sync import sync_all
from tests.integration.conftest import (  # noqa: F401 — 共用整合測試的資料庫 fixture
    alembic_cfg,
    anyio_backend,
    migrated,
    session,
    test_db_url,
)
from tests.integration.test_pipeline import (
    ADMIN,
    SkillScript,
    classification,
    deps,
    eid,
    extraction,
    relevance,
    risk,
    verification,
)
from tests.integration.test_public_api import Evidence, add_event
from tests.security.test_anonymous_report import (  # noqa: F401 — 共用匿名回報的 fixture
    CODE,
    REPORT_TEXT,
    REPORTS,
    Clients,
    client,
    clients,
    crawl,
    ingest,
    ingested_items,
    maker,
    stored,
    submit,
)

pytestmark = pytest.mark.anyio

STATUS = f"{REPORTS}/status"
SUPPLEMENTS = f"{REPORTS}/supplements"
RECEIPT = re.compile(r"^[A-Z2-7]{4}(-[A-Z2-7]{4}){5}$")
SUPPLEMENT_TEXT = "補充：閃爍的是靠近樓梯那三支燈管，今天早上還是一樣，旁邊的緊急照明也沒亮"


async def receipt_of(c: httpx2.AsyncClient, content: str = REPORT_TEXT) -> str:
    res = await submit(c, content)
    assert res.status_code == 202, res.text
    code = res.json()["data"]["receipt_code"]
    assert isinstance(code, str)
    return code


_fresh = iter(range(1, 10_000))


async def lookup(c: httpx2.AsyncClient | Clients, code: str | None) -> httpx2.Response:
    """查詢狀態；傳入 `Clients` 時每次換一個用戶端（查詢有每個用戶端的限速）。"""
    if isinstance(c, Clients):
        n = next(_fresh)
        c = c.at(f"10.200.{n // 250}.{n % 250 + 1}")
    headers = {} if code is None else {RECEIPT_HEADER: code}
    return await c.get(STATUS, headers=headers)


async def supplement(
    c: httpx2.AsyncClient, code: str, content: str = SUPPLEMENT_TEXT, **extra: Any
) -> httpx2.Response:
    return await c.post(SUPPLEMENTS, json={"receipt_code": code, "content": content, **extra})


def stable(res: httpx2.Response) -> tuple[int, Any, dict[str, str]]:
    """回應中與請求無關的部分（去掉每次不同的 request_id、timestamp）。"""
    body = res.json()
    volatile = ("request_id", "timestamp")
    meta = {k: v for k, v in (body.get("meta") or {}).items() if k not in volatile}
    headers = {
        k: v
        for k, v in res.headers.items()
        if k.lower() not in ("x-request-id", "date", "content-length")
    }
    return res.status_code, {"data": body["data"], "error": body["error"], "meta": meta}, headers


# ---------- 收據碼 ----------


async def test_submit_returns_high_entropy_receipt(client: httpx2.AsyncClient) -> None:  # noqa: F811
    res = await submit(client)
    assert res.status_code == 202
    data = res.json()["data"]
    assert data["status"] == "received"
    assert set(data) == {"status", "receipt_code"}
    assert RECEIPT.match(data["receipt_code"])
    assert res.headers["Cache-Control"] == "no-store"
    # 每次都不同
    other = await receipt_of(client, f"{REPORT_TEXT}（另一則）")
    assert other != data["receipt_code"]


async def test_database_has_no_plaintext_receipt(
    client: httpx2.AsyncClient,  # noqa: F811
    session: AsyncSession,  # noqa: F811
) -> None:
    code = await receipt_of(client)
    normalized = code.replace("-", "")
    [report] = await stored(session)
    [receipt] = list((await session.execute(select(ReportReceipt))).scalars())
    assert receipt.report_id == report.id
    assert receipt.code_hash == hashlib.sha256(normalized.encode()).hexdigest()
    assert receipt.is_supplement is False
    # 整個資料庫的文字欄位與稽核紀錄都沒有明文
    reports = await session.execute(select(AnonymousReport.content, AnonymousReport.location))
    hashes = await session.execute(select(ReportReceipt.code_hash))
    audits = list((await session.execute(select(AuditLog))).scalars())
    texts = json.dumps(
        [list(r) for r in reports.all()]
        + [list(r) for r in hashes.all()]
        + [[a.action, a.target, a.meta] for a in audits],
        ensure_ascii=False,
        default=str,
    )
    assert code not in texts
    assert normalized not in texts


async def test_receipt_not_logged(
    client: httpx2.AsyncClient,  # noqa: F811
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    code = await receipt_of(client)
    await lookup(client, code)
    await supplement(client, code)
    logged = "\n".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert code not in logged
    assert code.replace("-", "") not in logged


# ---------- 查詢 ----------


async def test_lookup_returns_only_status(clients: Clients) -> None:  # noqa: F811
    code = await receipt_of(clients.at())
    res = await lookup(clients, code)
    assert res.status_code == 200, res.text
    assert res.json()["data"] == {"status": "received"}
    assert REPORT_TEXT not in res.text
    # 收據碼查詢不可被共用快取
    assert res.headers["Cache-Control"] == "no-store"
    assert "ETag" not in res.headers
    # 大小寫、空白與連字號不影響
    loose = f"  {code.lower().replace('-', ' ')} "
    assert (await lookup(clients, loose)).json()["data"] == {"status": "received"}


async def test_wrong_and_missing_receipts_look_the_same(clients: Clients) -> None:  # noqa: F811
    client = clients.at()  # noqa: F811
    code = await receipt_of(client)
    wrong = ("A" if code[0] != "A" else "B") + code[1:]
    responses = [
        await lookup(clients, wrong),
        await lookup(clients, "ZZZZ-ZZZZ-ZZZZ-ZZZZ-ZZZZ-ZZZZ"),
        await lookup(clients, "not-a-receipt!"),
        await lookup(clients, "x" * 500),
        await lookup(clients, ""),
        await lookup(clients, None),
    ]
    first = stable(responses[0])
    assert first[0] == 404
    assert first[1]["error"]["code"] == "NOT_FOUND"
    for res in responses[1:]:
        assert stable(res) == first
    # 補件也一樣
    sup = [
        await supplement(clients.at("192.0.2.31"), wrong),
        await supplement(clients.at("192.0.2.32"), "ZZZZ-ZZZZ-ZZZZ-ZZZZ-ZZZZ-ZZZZ"),
        await supplement(clients.at("192.0.2.33"), "不是收據碼"),
    ]
    first_sup = stable(sup[0])
    assert first_sup[0] == 404
    assert first_sup[1] == first[1]
    for res in sup[1:]:
        assert stable(res) == first_sup


async def test_receipt_in_query_string_is_ignored(client: httpx2.AsyncClient) -> None:  # noqa: F811
    code = await receipt_of(client)
    res = await client.get(STATUS, params={"receipt_code": code})
    assert res.status_code == 404


async def test_status_follows_pipeline_and_event(
    clients: Clients,  # noqa: F811
    session: AsyncSession,  # noqa: F811
    maker: async_sessionmaker[AsyncSession],  # noqa: F811
) -> None:
    code = await receipt_of(clients.at())
    assert (await lookup(clients, code)).json()["data"] == {"status": "received"}

    await ingest(session, maker)
    [(item, _)] = await ingested_items(session)
    assert (await lookup(clients, code)).json()["data"] == {"status": "processing"}

    item.analysis_status = AnalysisStatus.DONE.value
    await session.commit()
    # 分析完成、沒有成立事件（例如判斷無關）
    assert (await lookup(clients, code)).json()["data"] == {"status": "handled"}

    event = Event(title="工程五館走廊照明故障", type="facility", status=EventStatus.VERIFIED.value)
    session.add(event)
    await session.flush()
    session.add(EventEvidence(event_id=event.id, source_item_id=item.id, support_type="supports"))
    await session.commit()
    assert (await lookup(clients, code)).json()["data"] == {"status": "processing"}

    event.status = EventStatus.RESOLVED.value
    await session.commit()
    res = await lookup(clients, code)
    assert res.json()["data"] == {"status": "handled"}
    assert "工程五館" not in res.text


async def test_lookups_are_rate_limited(clients: Clients) -> None:  # noqa: F811
    c = clients.at()
    code = await receipt_of(clients.at("192.0.2.50"))
    for _ in range(LOOKUP_CLIENT_LIMIT):
        assert (await lookup(c, "ZZZZ-ZZZZ-ZZZZ-ZZZZ-ZZZZ-ZZZZ")).status_code == 404
    res = await lookup(c, code)
    assert res.status_code == 429
    assert res.json()["error"]["code"] == "RATE_LIMITED"
    assert int(res.headers["Retry-After"]) >= 1
    # 其他用戶端不受影響
    assert (await lookup(clients.at("192.0.2.51"), code)).status_code == 200


# ---------- 補件 ----------


async def test_supplement_enters_inbox_with_same_receipt(
    client: httpx2.AsyncClient,  # noqa: F811
    session: AsyncSession,  # noqa: F811
) -> None:
    code = await receipt_of(client)
    res = await supplement(client, code, location="工程五館")
    assert res.status_code == 202, res.text
    assert res.json()["data"] == {"status": "received"}
    assert SUPPLEMENT_TEXT not in res.text
    assert res.headers["Cache-Control"] == "no-store"
    original, extra = await stored(session)
    assert extra.content == SUPPLEMENT_TEXT
    assert extra.location == "工程五館"
    receipts = list(
        (await session.execute(select(ReportReceipt).order_by(ReportReceipt.report_id))).scalars()
    )
    assert [(r.report_id, r.is_supplement) for r in receipts] == [
        (original.id, False),
        (extra.id, True),
    ]
    assert receipts[0].code_hash == receipts[1].code_hash
    # 補件之後狀態仍以同一張收據碼查詢
    assert (await lookup(client, code)).json()["data"] == {"status": "received"}
    audits = [a for a in (await session.execute(select(AuditLog))).scalars()]
    actions = [a.action for a in audits]
    assert actions == ["anonymous_report.submit", "anonymous_report.supplement"]
    dumped = json.dumps([[a.target, a.meta] for a in audits], ensure_ascii=False, default=str)
    assert SUPPLEMENT_TEXT not in dumped
    assert code not in dumped


async def test_supplement_has_same_format_limits(
    clients: Clients,  # noqa: F811
    session: AsyncSession,  # noqa: F811
) -> None:
    code = await receipt_of(clients.at())
    bad_bodies: list[dict[str, Any]] = [
        {"receipt_code": code, "content": "短"},
        {"receipt_code": code, "content": "長" * 2001},
        {"receipt_code": code, "content": f"{SUPPLEMENT_TEXT}\x00"},
        {"receipt_code": code, "content": SUPPLEMENT_TEXT, "location": "一\n二"},
        {"receipt_code": code, "content": SUPPLEMENT_TEXT, "contact": "me@example.com"},
        {"receipt_code": code},
        {"content": SUPPLEMENT_TEXT},
    ]
    for i, body in enumerate(bad_bodies):
        res = await clients.at(f"10.2.0.{i + 1}").post(SUPPLEMENTS, json=body)
        assert res.status_code == 422, (body, res.text)
        assert SUPPLEMENT_TEXT not in res.text
        assert code not in res.text
    res = await clients.at("10.2.1.1").post(
        SUPPLEMENTS, content=b"receipt_code=x", headers={"Content-Type": "text/plain"}
    )
    assert res.status_code == 422
    assert len(await stored(session)) == 1


async def test_supplements_share_report_rate_limit(
    clients: Clients,  # noqa: F811
    session: AsyncSession,  # noqa: F811
) -> None:
    c = clients.at()
    code = await receipt_of(c)
    for i in range(CLIENT_LIMIT - 1):
        assert (await supplement(c, code, f"{SUPPLEMENT_TEXT}（{i}）")).status_code == 202
    res = await supplement(c, code, f"{SUPPLEMENT_TEXT}（超過）")
    assert res.status_code == 429
    assert res.json()["error"]["code"] == "RATE_LIMITED"
    assert len(await stored(session)) == CLIENT_LIMIT
    # 錯的收據碼也算一次（不能拿補件端點暴力猜收據碼）
    other = clients.at("192.0.2.60")
    for _ in range(CLIENT_LIMIT):
        assert (await supplement(other, "ZZZZ-ZZZZ-ZZZZ-ZZZZ-ZZZZ-ZZZZ")).status_code == 404
    assert (await supplement(other, code)).status_code == 429


async def test_supplements_per_receipt_are_capped(
    clients: Clients,  # noqa: F811
    session: AsyncSession,  # noqa: F811
) -> None:
    code = await receipt_of(clients.at())
    for i in range(MAX_SUPPLEMENTS):
        res = await supplement(clients.at(f"10.1.0.{i + 1}"), code, f"{SUPPLEMENT_TEXT}（{i}）")
        assert res.status_code == 202, res.text
    res = await supplement(clients.at("10.1.1.1"), code, f"{SUPPLEMENT_TEXT}（超過）")
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "CONFLICT"
    assert len(await stored(session)) == 1 + MAX_SUPPLEMENTS


async def test_supplement_text_never_appears_on_public_endpoints(
    clients: Clients,  # noqa: F811
    session: AsyncSession,  # noqa: F811
    maker: async_sessionmaker[AsyncSession],  # noqa: F811
) -> None:
    reporter = clients.at()
    code = await receipt_of(reporter)
    secret = "補充：是機械系林小明同學弄壞的，他的電話 0912-345-678"
    assert (await supplement(reporter, code, secret)).status_code == 202
    await ingest(session, maker)
    texts = [body for _, body in await ingested_items(session)]
    assert len(texts) == 2
    assert any(secret in t for t in texts)
    await add_event(
        session,
        title="工程五館走廊照明故障（已查證）",
        summary="工程五館二樓走廊照明異常，已報修。",
        location="工程五館",
        evidence=(Evidence(CODE, texts[0]), Evidence(CODE, texts[1])),
        risk="high",
        confirm="user",
    )
    await sync_all(session)
    await session.commit()
    dumped = await crawl(clients.at("192.0.2.99"))
    for leak in (secret, "林小明", "0912-345-678", code, REPORT_TEXT):
        assert leak not in dumped, f"公開端出現 {leak!r}"


# ---------- 補件掛到原投稿的事件（管線） ----------


async def _original_and_supplement(
    clients: Clients,  # noqa: F811
    session: AsyncSession,  # noqa: F811
    maker: async_sessionmaker[AsyncSession],  # noqa: F811
) -> tuple[SourceItem, SourceItem]:
    reporter = clients.at()
    code = await receipt_of(reporter)
    assert (await supplement(reporter, code)).status_code == 202
    await ingest(session, maker)
    items = [item for item, _ in await ingested_items(session)]
    original, extra = sorted(items, key=lambda i: int((i.external_id or "").split("-")[1]))
    return original, extra


async def _event_with(session: AsyncSession, item: SourceItem, status: EventStatus) -> Event:  # noqa: F811
    event = Event(title="工程五館走廊照明故障", type="facility", status=status.value)
    session.add(event)
    await session.flush()
    session.add(EventEvidence(event_id=event.id, source_item_id=item.id, support_type="mentions"))
    await session.commit()
    return event


async def test_supplement_joins_original_event_without_linking_models(
    clients: Clients,  # noqa: F811
    session: AsyncSession,  # noqa: F811
    maker: async_sessionmaker[AsyncSession],  # noqa: F811
) -> None:
    original, extra = await _original_and_supplement(clients, session, maker)
    original.analysis_status = AnalysisStatus.DONE.value
    event = await _event_with(session, original, EventStatus.UNVERIFIED)
    provider = SkillScript(
        {
            "evidence-verifier": [
                verification(
                    {
                        eid(original): ("supports", "first_hand"),
                        eid(extra): ("supports", "first_hand"),
                    },
                    "unverified",
                )
            ],
            "risk-assessor": [risk(eid(original))],
        }
    )

    result = await analyze_item(session, extra.id, deps(provider))

    assert result.outcome is Outcome.LINKED
    assert result.event_id == event.id
    # 不經 S01–S04：補件直接掛到原投稿的事件，再重跑 S05、S06
    assert provider.called == ["evidence-verifier", "risk-assessor"]
    linked = await session.scalars(
        select(EventEvidence.event_id).where(EventEvidence.source_item_id == extra.id)
    )
    assert list(linked) == [event.id]
    await session.refresh(extra)
    assert extra.analysis_status == AnalysisStatus.DONE.value


async def test_supplement_waits_for_original_analysis(
    clients: Clients,  # noqa: F811
    session: AsyncSession,  # noqa: F811
    maker: async_sessionmaker[AsyncSession],  # noqa: F811
) -> None:
    original, extra = await _original_and_supplement(clients, session, maker)
    assert original.analysis_status == AnalysisStatus.PENDING.value
    provider = SkillScript({})

    result = await analyze_item(session, extra.id, deps(provider))

    assert result.outcome is Outcome.DEFERRED
    assert provider.called == []
    await session.refresh(extra)
    # 回到待分析、不計失敗次數，下一輪掃描再試
    assert extra.analysis_status == AnalysisStatus.PENDING.value
    assert extra.analysis_attempts == 0


class Recording(SkillScript):
    """另外記下每次呼叫的使用者訊息（確認原投稿有被一起交給模型）。"""

    def __init__(self, scripts: Any) -> None:
        super().__init__(scripts)
        self.inputs: list[str] = []

    async def generate_structured(self, messages: Any, *args: Any, **kwargs: Any) -> Any:
        self.inputs.append(messages[-1].content)
        return await super().generate_structured(messages, *args, **kwargs)


def report_extraction(extra: SourceItem, original: SourceItem) -> dict[str, Any]:
    """S02 的輸出：地點引用原投稿（只有原投稿寫了「工程五館」）。"""
    out = extraction(eid(extra), location="工程五館")
    out["evidence_ids"] = [eid(extra), eid(original)]
    out["evidence_spans"] = [
        {"field": "location", "evidence_id": eid(original), "quote": "工程五館"}
    ]
    return out


async def test_supplement_without_event_is_analyzed_with_original_as_context(
    clients: Clients,  # noqa: F811
    session: AsyncSession,  # noqa: F811
    maker: async_sessionmaker[AsyncSession],  # noqa: F811
) -> None:
    original, extra = await _original_and_supplement(clients, session, maker)
    original.analysis_status = AnalysisStatus.DONE.value  # 例如原投稿單獨看不算事件
    await session.commit()
    provider = Recording({"relevance-filter": [relevance(eid(extra), relevant=False)]})

    result = await analyze_item(session, extra.id, deps(provider))

    assert result.outcome is Outcome.IRRELEVANT
    assert provider.called == ["relevance-filter"]
    [prompt] = provider.inputs
    # 補件在前（主要文章），原投稿在後；兩份都包在各自的 <untrusted_source> 中
    assert prompt.count("<untrusted_source") == 2
    assert prompt.index(eid(extra)) < prompt.index(eid(original))
    assert prompt.index(SUPPLEMENT_TEXT) < prompt.index(REPORT_TEXT)


async def test_supplement_creating_event_brings_original_along(
    clients: Clients,  # noqa: F811
    session: AsyncSession,  # noqa: F811
    maker: async_sessionmaker[AsyncSession],  # noqa: F811
) -> None:
    original, extra = await _original_and_supplement(clients, session, maker)
    original.analysis_status = AnalysisStatus.DONE.value
    await session.commit()
    both = {
        eid(extra): ("supports", "first_hand"),
        eid(original): ("supports", "first_hand"),
    }
    provider = Recording(
        {
            "relevance-filter": [relevance(eid(extra))],
            "event-extractor": [report_extraction(extra, original)],
            "event-classifier": [classification(eid(extra))],
            "evidence-verifier": [verification(both, "unverified")],
            "risk-assessor": [risk(eid(extra))],
        }
    )

    result = await analyze_item(session, extra.id, deps(provider))

    assert result.outcome is Outcome.CREATED
    # S02、S03 也看得到原投稿
    for prompt in provider.inputs[:3]:
        assert REPORT_TEXT in prompt
    rows = await session.execute(
        select(EventEvidence.source_item_id).where(EventEvidence.event_id == result.event_id)
    )
    assert set(rows.scalars()) == {original.id, extra.id}


async def test_original_in_archived_event_is_context_but_not_attached(
    clients: Clients,  # noqa: F811
    session: AsyncSession,  # noqa: F811
    maker: async_sessionmaker[AsyncSession],  # noqa: F811
) -> None:
    original, extra = await _original_and_supplement(clients, session, maker)
    original.analysis_status = AnalysisStatus.DONE.value
    archived = await _event_with(session, original, EventStatus.ARCHIVED)
    both = {
        eid(extra): ("supports", "first_hand"),
    }
    provider = Recording(
        {
            "relevance-filter": [relevance(eid(extra))],
            "event-extractor": [report_extraction(extra, original)],
            "event-classifier": [classification(eid(extra))],
            "evidence-verifier": [verification(both, "unverified")],
            "risk-assessor": [risk(eid(extra))],
        }
    )

    result = await analyze_item(session, extra.id, deps(provider))

    assert result.outcome is Outcome.CREATED
    assert result.event_id != archived.id
    rows = await session.execute(
        select(EventEvidence.source_item_id).where(EventEvidence.event_id == result.event_id)
    )
    # 使用者封存過的原投稿不會被帶進新事件
    assert set(rows.scalars()) == {extra.id}


async def test_original_report_is_unaffected(
    clients: Clients,  # noqa: F811
    session: AsyncSession,  # noqa: F811
    maker: async_sessionmaker[AsyncSession],  # noqa: F811
) -> None:
    original, _ = await _original_and_supplement(clients, session, maker)
    provider = SkillScript({"relevance-filter": [relevance(eid(original), relevant=False)]})
    result = await analyze_item(session, original.id, deps(provider))
    assert result.outcome is Outcome.IRRELEVANT


async def test_status_follows_merged_event(
    clients: Clients,  # noqa: F811
    session: AsyncSession,  # noqa: F811
    maker: async_sessionmaker[AsyncSession],  # noqa: F811
) -> None:
    reporter = clients.at()
    code = await receipt_of(reporter)
    await ingest(session, maker)
    [(item, _)] = await ingested_items(session)
    item.analysis_status = AnalysisStatus.DONE.value
    merged = await _event_with(session, item, EventStatus.UNVERIFIED)
    target = Event(title="工程五館照明", type="facility", status=EventStatus.VERIFIED.value)
    session.add(target)
    await session.commit()

    await merge_events(session, ADMIN, merged.id, target.id, request_id="req-merge")
    await session.commit()
    assert (await lookup(clients, code)).json()["data"] == {"status": "processing"}

    target.status = EventStatus.RESOLVED.value
    await session.commit()
    assert (await lookup(clients, code)).json()["data"] == {"status": "handled"}


async def test_admin_evidence_marks_supplements(
    clients: Clients,  # noqa: F811
    session: AsyncSession,  # noqa: F811
    maker: async_sessionmaker[AsyncSession],  # noqa: F811
) -> None:
    original, extra = await _original_and_supplement(clients, session, maker)
    event = await _event_with(session, original, EventStatus.UNVERIFIED)
    session.add(EventEvidence(event_id=event.id, source_item_id=extra.id, support_type="mentions"))
    await session.commit()
    clients.app.dependency_overrides[get_principal] = lambda: ADMIN

    res = await clients.at().get(f"/api/v1/admin/events/{event.id}")

    assert res.status_code == 200, res.text
    marks = {e["source_item_id"]: e.get("supplement_of") for e in res.json()["data"]["evidence"]}
    assert marks == {str(original.id): None, str(extra.id): str(original.id)}
    # 不是補件的證據不帶這個欄位（既有回應格式不變）
    [plain] = [e for e in res.json()["data"]["evidence"] if e["source_item_id"] == str(original.id)]
    assert "supplement_of" not in plain
