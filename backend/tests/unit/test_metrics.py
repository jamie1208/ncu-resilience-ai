"""P5-T04：Prometheus 指標（任務成功率、AI 延遲與費用、公開 API 錯誤率與限速次數）。"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY, generate_latest

from app.ai.llm.config import LLMConfig
from app.api.deps import get_db_session
from app.core import metrics
from app.core.config import get_settings
from app.main import create_app
from tests.unit.ai.test_runner import OK, config, run, skill  # noqa: F401 — 共用 fixture

pytestmark = pytest.mark.anyio


def value(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


class Limited:
    """一定回 429 的限速器。"""

    def hit(self, client: str) -> int | None:
        return 7


@pytest.fixture()
def client() -> TestClient:
    app = create_app()

    async def no_session() -> AsyncIterator[Any]:
        yield None

    app.dependency_overrides[get_db_session] = no_session
    return TestClient(app)


# ---------- 任務 ----------


def test_task_runs_are_counted_by_task_and_outcome() -> None:
    ok = value("ncu_task_runs_total", task="t.demo", outcome="success")
    bad = value("ncu_task_runs_total", task="t.demo", outcome="failure")
    count = value("ncu_task_duration_seconds_count", task="t.demo")

    metrics.observe_task("t.demo", "SUCCESS", 0.5)
    metrics.observe_task("t.demo", "FAILURE", 1.5)
    metrics.observe_task("t.demo", "RETRY", None)

    assert value("ncu_task_runs_total", task="t.demo", outcome="success") == ok + 1
    assert value("ncu_task_runs_total", task="t.demo", outcome="failure") == bad + 1
    assert value("ncu_task_runs_total", task="t.demo", outcome="retry") >= 1
    assert value("ncu_task_duration_seconds_count", task="t.demo") == count + 2


def test_unknown_task_state_is_bucketed() -> None:
    metrics.observe_task("t.demo", "WEIRD", None)
    assert value("ncu_task_runs_total", task="t.demo", outcome="other") >= 1


# ---------- AI ----------


def test_ai_run_records_latency_cost_status_and_daily_limit() -> None:
    cost = value("ncu_ai_cost_usd_total", skill="s-demo", model="m-demo")
    runs = value("ncu_ai_runs_total", skill="s-demo", status="succeeded")
    latency = value("ncu_ai_run_latency_seconds_sum", skill="s-demo")

    metrics.observe_ai_run(
        "s-demo", "m-demo", "succeeded", latency_ms=1500, cost_usd=0.002, daily_limit_usd=1.0
    )

    assert value("ncu_ai_cost_usd_total", skill="s-demo", model="m-demo") == pytest.approx(
        cost + 0.002
    )
    assert value("ncu_ai_runs_total", skill="s-demo", status="succeeded") == runs + 1
    assert value("ncu_ai_run_latency_seconds_sum", skill="s-demo") == pytest.approx(latency + 1.5)
    assert value("ncu_ai_daily_cost_limit_usd") == 1.0


async def test_run_skill_reports_metrics(skill: Any, config: LLMConfig) -> None:  # noqa: F811
    before = value("ncu_ai_runs_total", skill="template", status="succeeded")
    cost = value("ncu_ai_cost_usd_total", skill="template", model=config.default_model)

    await run(skill, config, [OK], event_id=None, trace_id="t-1")

    assert value("ncu_ai_runs_total", skill="template", status="succeeded") == before + 1
    assert value(
        "ncu_ai_cost_usd_total", skill="template", model=config.default_model
    ) == pytest.approx(cost + 0.00065)
    assert value("ncu_ai_daily_cost_limit_usd") == config.limits.daily_usd


# ---------- HTTP ----------


@pytest.mark.parametrize(
    ("path", "surface"),
    [
        ("/api/v1/public/feed", "public"),
        ("/api/v1/admin/events", "admin"),
        ("/api/v1/auth/login", "auth"),
        ("/api/v1/health", "other"),
        ("/api/v1/publicity", "other"),
    ],
)
def test_surface_of(path: str, surface: str) -> None:
    assert metrics.surface_of(path) == surface


def test_requests_are_counted_by_route_template(client: TestClient) -> None:
    before = value(
        "ncu_http_requests_total",
        surface="other",
        route="/api/v1/health",
        method="GET",
        status_class="2xx",
    )
    client.get("/api/v1/health")
    assert (
        value(
            "ncu_http_requests_total",
            surface="other",
            route="/api/v1/health",
            method="GET",
            status_class="2xx",
        )
        == before + 1
    )


def test_unmatched_paths_do_not_create_raw_path_labels(client: TestClient) -> None:
    raw = f"/api/v1/public/nope/{uuid.uuid4()}"
    before = value(
        "ncu_http_requests_total",
        surface="public",
        route="unmatched",
        method="GET",
        status_class="4xx",
    )
    client.get(raw)
    assert (
        value(
            "ncu_http_requests_total",
            surface="public",
            route="unmatched",
            method="GET",
            status_class="4xx",
        )
        == before + 1
    )
    assert raw.encode() not in generate_latest(REGISTRY)


def test_path_parameters_are_replaced_by_their_names(client: TestClient) -> None:
    event_id = str(uuid.uuid4())
    res = client.get(f"/api/v1/admin/events/{event_id}")

    assert res.status_code == 401
    assert (
        value(
            "ncu_http_requests_total",
            surface="admin",
            route="/api/v1/admin/events/{event_id}",
            method="GET",
            status_class="4xx",
        )
        >= 1
    )
    assert event_id.encode() not in generate_latest(REGISTRY)


def test_public_rate_limit_is_counted(client: TestClient) -> None:
    client.app.state.public_rate_limiter = Limited()  # type: ignore[attr-defined]
    before = value("ncu_public_rate_limited_total", limiter="public_read")

    res = client.get("/api/v1/public/feed")

    assert res.status_code == 429
    assert value("ncu_public_rate_limited_total", limiter="public_read") == before + 1
    assert (
        value(
            "ncu_http_requests_total",
            surface="public",
            route="/api/v1/public/feed",
            method="GET",
            status_class="4xx",
        )
        >= 1
    )


def test_report_rate_limit_is_counted(client: TestClient) -> None:
    client.app.state.report_rate_limiter = Limited()  # type: ignore[attr-defined]
    before = value("ncu_public_rate_limited_total", limiter="report")

    res = client.post("/api/v1/public/reports", json={"content": "測試內容超過十個字元"})

    assert res.status_code == 429
    assert value("ncu_public_rate_limited_total", limiter="report") == before + 1


def test_server_errors_are_counted_as_5xx() -> None:
    app = create_app()

    @app.get("/api/v1/public/boom")
    async def boom() -> None:
        raise RuntimeError("boom")

    before = value(
        "ncu_http_requests_total",
        surface="public",
        route="/api/v1/public/boom",
        method="GET",
        status_class="5xx",
    )
    res = TestClient(app, raise_server_exceptions=False).get("/api/v1/public/boom")

    assert res.status_code == 500
    assert (
        value(
            "ncu_http_requests_total",
            surface="public",
            route="/api/v1/public/boom",
            method="GET",
            status_class="5xx",
        )
        == before + 1
    )


def test_exposition_has_no_client_address_or_request_id(client: TestClient) -> None:
    res = client.get("/api/v1/health", headers={"X-Request-ID": "trace-secret-0001"})
    text = generate_latest(REGISTRY)
    assert res.status_code == 200
    assert b"trace-secret-0001" not in text
    assert b"testclient" not in text


# ---------- 指標伺服器 ----------


def test_metrics_server_starts_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[int, str]] = []
    monkeypatch.setattr(metrics, "start_http_server", lambda port, addr: calls.append((port, addr)))
    monkeypatch.setattr(metrics, "_server_port", None)

    metrics.start_metrics_server(9100)
    metrics.start_metrics_server(9100)

    assert calls == [(9100, "0.0.0.0")]  # noqa: S104 — 只在 Docker 內部網路，不對主機發布


def test_api_starts_metrics_server_only_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr(metrics, "start_metrics_server", calls.append)

    monkeypatch.delenv("NCU_METRICS_PORT", raising=False)
    get_settings.cache_clear()
    create_app()
    monkeypatch.setenv("NCU_METRICS_PORT", "9100")
    get_settings.cache_clear()
    try:
        create_app()
    finally:
        monkeypatch.delenv("NCU_METRICS_PORT")
        get_settings.cache_clear()

    assert calls == [9100]


def test_rate_limit_series_exist_before_first_hit() -> None:
    """序列先以 0 出現，Prometheus 的 increase() 才看得到第一次限速。"""
    for limiter in ("public_read", "report"):
        assert (
            REGISTRY.get_sample_value("ncu_public_rate_limited_total", {"limiter": limiter})
            is not None
        )
