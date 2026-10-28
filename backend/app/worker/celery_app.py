"""Celery 應用程式（P1-T07）：Redis 為 broker，不保存任務結果。

啟動（Docker Compose 會自動執行）：
- worker：`celery -A app.worker.celery_app worker --concurrency=1`
- beat：`celery -A app.worker.celery_app beat`

worker 只開一個行程：限速器（`HostRateLimiter`）是行程內共用，單一行程才能保證
「同一網域同時最多一個請求」。

可觀測性（P5-T04）：
- 送出任務時，把目前的 request_id 放進 Celery 標頭 `trace_id`；worker 執行前綁定到
  `request_id_var`，日誌、稽核與 AI run 因此沿用 API 的 trace（沒有標頭時另產生一個）；
- 任務結束時記錄成功率與執行時間；worker 子行程在 NCU_METRICS_PORT 輸出 /metrics；
- worker 與 beat 的日誌改為與 API 相同的 JSON 格式（含 request_id）。
"""

from __future__ import annotations

import re
import time
import uuid
from contextvars import Token
from typing import Any

from celery import Celery, signals

from app.ai.llm.config import load_llm_config
from app.core.config import get_settings
from app.core.logging import configure_logging, request_id_var
from app.core.metrics import observe_task, set_daily_cost_limit, start_metrics_server

DISPATCH_TASK = "ingest.dispatch_due_sources"
INGEST_TASK = "ingest.ingest_source"
ANALYZE_ITEM_TASK = "pipeline.analyze_item"  # P2-T13
ANALYZE_EVENT_TASK = "pipeline.analyze_event"
ANALYZE_DISPATCH_TASK = "pipeline.dispatch_pending_items"  # P4-T07
REPORT_EVENT_TASK = "reports.generate_event_report"  # P4-T09
SYNC_PUBLICATIONS_TASK = "publishing.sync_publications"  # P5-T11
DISPATCH_EVERY_SECONDS = 60.0

celery_app = Celery(
    "ncu_resilience",
    broker=get_settings().redis_url,
    include=[
        "app.worker.tasks.ingest",
        "app.pipeline.analyze_item",
        "app.reports.service",
        "app.publishing.sync",
    ],
)
celery_app.conf.update(
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,  # worker 中途當機時，任務會被重新投遞
    worker_prefetch_multiplier=1,
    task_ignore_result=True,
    task_soft_time_limit=15 * 60,
    task_time_limit=20 * 60,
    broker_connection_retry_on_startup=True,
    beat_schedule={
        "dispatch-due-sources": {"task": DISPATCH_TASK, "schedule": DISPATCH_EVERY_SECONDS},
        "dispatch-pending-items": {
            "task": ANALYZE_DISPATCH_TASK,
            "schedule": DISPATCH_EVERY_SECONDS,
        },
        # 公開狀態同步（P5-T11）：不再符合公開規則的事件最晚一分鐘內撤下
        "sync-publications": {
            "task": SYNC_PUBLICATIONS_TASK,
            "schedule": DISPATCH_EVERY_SECONDS,
        },
    },
)


TRACE_HEADER = "trace_id"
_VALID_TRACE = re.compile(r"^[A-Za-z0-9-]{8,64}$")
_running: dict[str, tuple[Token[str | None], float]] = {}


@signals.before_task_publish.connect
def propagate_trace(headers: dict[str, Any] | None = None, **_: Any) -> None:
    """送出任務時帶上目前的 trace（已有就不覆蓋）。"""
    current = request_id_var.get()
    if headers is not None and current and TRACE_HEADER not in headers:
        headers[TRACE_HEADER] = current


@signals.task_prerun.connect
def bind_trace(task_id: str, task: Any, **_: Any) -> None:
    """執行任務前綁定 trace；標頭格式不安全或沒有時另產生一個（避免日誌注入）。"""
    incoming = task.request.get(TRACE_HEADER)
    trace = incoming if isinstance(incoming, str) and _VALID_TRACE.fullmatch(incoming) else None
    token = request_id_var.set(trace or uuid.uuid4().hex)
    _running[task_id] = (token, time.perf_counter())


@signals.task_postrun.connect
def release_trace(task_id: str, task: Any, state: str | None = None, **_: Any) -> None:
    """任務結束：記錄結果與執行時間，還原 trace。"""
    started = _running.pop(task_id, None)
    seconds = time.perf_counter() - started[1] if started else None
    observe_task(task.name, state or "", seconds)
    if started:
        request_id_var.reset(started[0])


@signals.worker_process_init.connect
def start_worker_metrics(**_: Any) -> None:
    """worker 子行程（實際執行任務的行程）啟動時輸出 /metrics，並先寫入每日費用上限。"""
    port = get_settings().metrics_port
    if port is not None:
        start_metrics_server(port)
        set_daily_cost_limit(load_llm_config().limits.daily_usd)


@signals.setup_logging.connect
def use_json_logging(**_: Any) -> None:
    """接管 Celery 的日誌設定，改用 JSON 格式。"""
    configure_logging(get_settings().log_level)
