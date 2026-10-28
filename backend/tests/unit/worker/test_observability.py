"""P5-T04：Celery 任務的 trace_id 傳遞、任務指標、worker 指標伺服器與 JSON 日誌（不連 Redis）。"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from typing import Any

import pytest
from prometheus_client import REGISTRY

from app.core import logging as app_logging
from app.core.logging import JsonFormatter, request_id_var
from app.worker import celery_app as app_module


def fake_task(name: str = "t.observed", **headers: Any) -> Any:
    request = SimpleNamespace(**headers)
    request.get = lambda key, default=None: getattr(request, key, default)
    return SimpleNamespace(name=name, request=request)


def runs(outcome: str, task: str = "t.observed") -> float:
    labels = {"task": task, "outcome": outcome}
    return REGISTRY.get_sample_value("ncu_task_runs_total", labels) or 0.0


@pytest.fixture(autouse=True)
def clean_context() -> Any:
    token = request_id_var.set(None)
    yield
    request_id_var.reset(token)


# ---------- 送出任務 ----------


def test_publish_adds_current_trace_id_header() -> None:
    request_id_var.set("api-req-0001")
    headers: dict[str, Any] = {}
    app_module.propagate_trace(headers=headers)
    assert headers["trace_id"] == "api-req-0001"


def test_publish_keeps_existing_trace_header() -> None:
    request_id_var.set("api-req-0001")
    headers: dict[str, Any] = {"trace_id": "earlier-0001"}
    app_module.propagate_trace(headers=headers)
    assert headers["trace_id"] == "earlier-0001"


def test_publish_without_context_adds_nothing() -> None:
    headers: dict[str, Any] = {}
    app_module.propagate_trace(headers=headers)
    assert "trace_id" not in headers


def test_signal_handlers_are_connected() -> None:
    from celery import signals

    receivers = {
        signals.before_task_publish: app_module.propagate_trace,
        signals.task_prerun: app_module.bind_trace,
        signals.task_postrun: app_module.release_trace,
        signals.worker_process_init: app_module.start_worker_metrics,
        signals.setup_logging: app_module.use_json_logging,
    }
    for signal, handler in receivers.items():
        connected = [ref() for _, ref in signal.receivers]
        assert handler in connected, signal.name


# ---------- 執行任務 ----------


def test_prerun_binds_trace_from_header_and_postrun_records_success() -> None:
    task = fake_task(trace_id="api-req-0002")
    before = runs("success")

    app_module.bind_trace(task_id="id-1", task=task)
    assert request_id_var.get() == "api-req-0002"
    app_module.release_trace(task_id="id-1", task=task, state="SUCCESS")

    assert request_id_var.get() is None
    assert runs("success") == before + 1


def test_prerun_without_header_generates_trace() -> None:
    task = fake_task()
    app_module.bind_trace(task_id="id-2", task=task)
    generated = request_id_var.get()
    app_module.release_trace(task_id="id-2", task=task, state="FAILURE")

    assert generated and len(generated) >= 16
    assert request_id_var.get() is None


def test_unsafe_trace_header_is_replaced() -> None:
    task = fake_task(trace_id="bad\nvalue")
    app_module.bind_trace(task_id="id-3", task=task)
    bound = request_id_var.get()
    app_module.release_trace(task_id="id-3", task=task, state="SUCCESS")
    assert bound and "\n" not in bound


def test_postrun_records_failure() -> None:
    task = fake_task()
    before = runs("failure")
    app_module.bind_trace(task_id="id-4", task=task)
    app_module.release_trace(task_id="id-4", task=task, state="FAILURE")
    assert runs("failure") == before + 1


def test_task_logs_carry_trace_id(caplog: pytest.LogCaptureFixture) -> None:
    task = fake_task(trace_id="api-req-0005")
    app_module.bind_trace(task_id="id-5", task=task)
    with caplog.at_level(logging.INFO):
        logging.getLogger("app.test").info("working")
    line = JsonFormatter().format(caplog.records[-1])
    app_module.release_trace(task_id="id-5", task=task, state="SUCCESS")
    assert json.loads(line)["request_id"] == "api-req-0005"


# ---------- worker 行程 ----------


def test_worker_metrics_server_starts_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    ports: list[int] = []
    monkeypatch.setattr(app_module, "start_metrics_server", ports.append)
    monkeypatch.setattr(app_module, "get_settings", lambda: SimpleNamespace(metrics_port=9100))
    app_module.start_worker_metrics()
    monkeypatch.setattr(app_module, "get_settings", lambda: SimpleNamespace(metrics_port=None))
    app_module.start_worker_metrics()
    assert ports == [9100]


def test_worker_publishes_daily_cost_limit_on_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """worker 重啟後、第一次 AI 呼叫前，費用告警也要有上限可比較。"""
    monkeypatch.setattr(app_module, "start_metrics_server", lambda port: None)
    monkeypatch.setattr(app_module, "get_settings", lambda: SimpleNamespace(metrics_port=9100))
    limits = SimpleNamespace(limits=SimpleNamespace(daily_usd=2.5))
    monkeypatch.setattr(app_module, "load_llm_config", lambda: limits)
    app_module.start_worker_metrics()
    assert REGISTRY.get_sample_value("ncu_ai_daily_cost_limit_usd") == 2.5


def test_worker_uses_json_logging(monkeypatch: pytest.MonkeyPatch) -> None:
    levels: list[str] = []
    monkeypatch.setattr(app_logging, "configure_logging", levels.append)
    monkeypatch.setattr(app_module, "configure_logging", levels.append)
    monkeypatch.setattr(app_module, "get_settings", lambda: SimpleNamespace(log_level="WARNING"))
    app_module.use_json_logging()
    assert levels == ["WARNING"]
