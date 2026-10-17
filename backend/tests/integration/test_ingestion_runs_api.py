"""P1-T08：GET /api/v1/admin/ingestion-runs 與 /health（統一回應格式、權限、cursor 分頁）。"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session, get_principal
from app.core.security import ANONYMOUS, Principal, Role
from app.main import create_app
from app.models.source import IngestionRun, Source

pytestmark = pytest.mark.anyio

URL = "/api/v1/admin/ingestion-runs"
ADMIN = Principal(kind="admin", subject="u-1", roles=frozenset({Role.ADMIN}))
INGESTION = Principal(
    kind="service", subject="ingestion", roles=frozenset({Role.SERVICE_INGESTION})
)
# 健康狀態以實際的現在時間計算 7 天窗口，所以測試資料放在一天前
T0 = datetime.now(UTC).replace(microsecond=0) - timedelta(days=1)


class Api:
    def __init__(self, app: FastAPI, client: httpx2.AsyncClient) -> None:
        self.app = app
        self.client = client

    def as_principal(self, principal: Principal) -> None:
        self.app.dependency_overrides[get_principal] = lambda: principal

    async def get(self, path: str = URL, **params: Any) -> httpx2.Response:
        return await self.client.get(path, params=params)


@pytest.fixture()
async def api(session: AsyncSession) -> AsyncIterator[Api]:
    app = create_app()

    async def test_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_db_session] = test_session
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        api = Api(app, client)
        api.as_principal(ADMIN)
        yield api


async def seed(session: AsyncSession) -> dict[str, Source]:
    """兩個來源，各有數筆執行紀錄；時間間隔 1 小時，ncu-announcements 5 筆、ncu-sec 2 筆。"""
    sources = {
        code: Source(
            code=code, name=code, type="rpage", access_policy="approved", poll_interval=3600
        )
        for code in ("ncu-announcements", "ncu-sec")
    }
    session.add_all(sources.values())
    await session.flush()
    plan = [
        ("ncu-announcements", "succeeded"),
        ("ncu-sec", "succeeded"),
        ("ncu-announcements", "failed"),
        ("ncu-announcements", "succeeded"),
        ("ncu-sec", "retrying"),
        ("ncu-announcements", "succeeded"),
        ("ncu-announcements", "running"),
    ]
    for i, (code, status) in enumerate(plan):
        started = T0 + timedelta(hours=i)
        session.add(
            IngestionRun(
                source_id=sources[code].id,
                started_at=started,
                finished_at=started + timedelta(minutes=2)
                if status not in ("running", "retrying")
                else None,
                status=status,
                counts={"received": i} if status == "succeeded" else {},
            )
        )
    await session.commit()
    return sources


# --- 回應格式與內容 --------------------------------------------------------------------


async def test_lists_runs_newest_first_with_envelope(api: Api, session: AsyncSession) -> None:
    await seed(session)
    res = await api.get()
    assert res.status_code == 200
    body = res.json()
    assert set(body) == {"data", "meta", "error"}
    assert body["error"] is None
    runs = body["data"]
    assert len(runs) == 7
    assert [r["status"] for r in runs[:3]] == ["running", "succeeded", "retrying"]
    first = runs[1]
    assert set(first) == {"id", "source_code", "status", "started_at", "finished_at", "counts"}
    assert first["source_code"] == "ncu-announcements"
    assert first["counts"] == {"received": 5}
    assert datetime.fromisoformat(first["started_at"]) == T0 + timedelta(hours=5)
    assert datetime.fromisoformat(first["started_at"]).utcoffset() == timedelta(0)
    assert runs[0]["finished_at"] is None
    uuid.UUID(body["meta"]["request_id"])
    assert body["meta"]["next_cursor"] is None


async def test_filters_by_source_and_status(api: Api, session: AsyncSession) -> None:
    await seed(session)
    res = await api.get(source="ncu-sec")
    assert [r["status"] for r in res.json()["data"]] == ["retrying", "succeeded"]

    res = await api.get(source="ncu-announcements", status="succeeded")
    data = res.json()["data"]
    assert len(data) == 3
    assert {r["source_code"] for r in data} == {"ncu-announcements"}


async def test_unknown_source_returns_empty_list(api: Api, session: AsyncSession) -> None:
    await seed(session)
    res = await api.get(source="no-such-source")
    assert res.status_code == 200
    assert res.json()["data"] == []


# --- cursor 分頁 -----------------------------------------------------------------------


async def test_cursor_pagination_walks_all_runs_without_overlap(
    api: Api, session: AsyncSession
) -> None:
    await seed(session)
    seen: list[str] = []
    cursor: str | None = None
    pages = 0
    while True:
        params: dict[str, Any] = {"limit": 3}
        if cursor:
            params["cursor"] = cursor
        body = (await api.get(**params)).json()
        seen.extend(r["id"] for r in body["data"])
        assert body["meta"]["limit"] == 3
        cursor = body["meta"]["next_cursor"]
        pages += 1
        if cursor is None:
            break
    assert pages == 3
    assert len(seen) == len(set(seen)) == 7


async def test_runs_with_same_start_time_are_not_skipped(api: Api, session: AsyncSession) -> None:
    src = Source(code="ncu-aa", name="aa", type="rpage", access_policy="approved")
    session.add(src)
    await session.flush()
    for _ in range(4):
        session.add(IngestionRun(source_id=src.id, started_at=T0, status="succeeded", counts={}))
    await session.commit()
    first = (await api.get(limit=2)).json()
    second = (await api.get(limit=2, cursor=first["meta"]["next_cursor"])).json()
    ids = [r["id"] for r in first["data"] + second["data"]]
    assert len(set(ids)) == 4
    assert second["meta"]["next_cursor"] is None


# --- 參數驗證 --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 101},
        {"limit": "many"},
        {"status": "exploded"},
        {"cursor": "not-a-cursor"},
        {"cursor": "eyJ0IjogMX0"},  # 合法 base64、內容不對
    ],
)
async def test_invalid_query_returns_validation_error(
    api: Api, session: AsyncSession, params: dict[str, Any]
) -> None:
    res = await api.get(**params)
    assert res.status_code == 422
    body = res.json()
    assert body["data"] is None
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["meta"]["request_id"]
    # 不回顯使用者輸入
    assert "exploded" not in res.text
    assert "not-a-cursor" not in res.text


# --- 權限 ------------------------------------------------------------------------------


@pytest.mark.parametrize("path", [URL, f"{URL}/health"])
async def test_anonymous_is_unauthorized(api: Api, session: AsyncSession, path: str) -> None:
    api.as_principal(ANONYMOUS)
    res = await api.get(path)
    assert res.status_code == 401
    body = res.json()
    assert body["error"]["code"] == "UNAUTHORIZED"
    assert body["data"] is None


@pytest.mark.parametrize("path", [URL, f"{URL}/health"])
async def test_principal_without_permission_is_forbidden(
    api: Api, session: AsyncSession, path: str
) -> None:
    api.as_principal(INGESTION)  # 服務帳號沒有 ingestion_runs:read
    res = await api.get(path)
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "FORBIDDEN"


async def test_default_principal_is_anonymous_until_login_exists(session: AsyncSession) -> None:
    # P3-T01 實作登入前，沒有任何方式通過管理 API 的驗證
    app = create_app()

    async def test_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_db_session] = test_session
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get(URL, headers={"Authorization": "Bearer anything"})
    assert res.status_code == 401


# --- 健康狀態 --------------------------------------------------------------------------


async def test_health_endpoint_reports_each_source(api: Api, session: AsyncSession) -> None:
    await seed(session)
    res = await api.get(f"{URL}/health")
    assert res.status_code == 200
    body = res.json()
    assert body["meta"]["window_days"] == 7
    data = {h["code"]: h for h in body["data"]}
    assert set(data) == {"ncu-announcements", "ncu-sec"}
    ann = data["ncu-announcements"]
    assert set(ann) == {
        "code",
        "name",
        "enabled",
        "access_policy",
        "poll_interval",
        "status",
        "last_run_status",
        "last_run_at",
        "last_success_at",
        "consecutive_failures",
        "runs_in_window",
        "success_rate",
        "open_dead_letters",
    }
    assert ann["runs_in_window"] == 4  # 執行中的不算
    assert ann["success_rate"] == pytest.approx(0.75)
