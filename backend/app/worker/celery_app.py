"""Celery 應用程式（P1-T07）：Redis 為 broker，不保存任務結果。

啟動（Docker Compose 會自動執行）：
- worker：`celery -A app.worker.celery_app worker --concurrency=1`
- beat：`celery -A app.worker.celery_app beat`

worker 只開一個行程：限速器（`HostRateLimiter`）是行程內共用，單一行程才能保證
「同一網域同時最多一個請求」。
"""

from __future__ import annotations

from celery import Celery

from app.core.config import get_settings

DISPATCH_TASK = "ingest.dispatch_due_sources"
INGEST_TASK = "ingest.ingest_source"
DISPATCH_EVERY_SECONDS = 60.0

celery_app = Celery(
    "ncu_resilience",
    broker=get_settings().redis_url,
    include=["app.worker.tasks.ingest"],
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
    },
)
