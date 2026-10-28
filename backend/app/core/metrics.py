"""Prometheus 指標（P5-T04）：任務成功率、AI 延遲與費用、公開 API 錯誤率與限速次數。

- api 與 worker 各自在行程內累計，以 `start_metrics_server()` 在獨立埠（NCU_METRICS_PORT）
  輸出 `/metrics`；這個埠只在 Docker 內部網路給 Prometheus 抓取，不發布到主機、不經通道。
- 標籤只用有限集合的值（任務名稱、Skill 名稱、路由樣板、狀態類別），
  不放原始路徑、request_id、用戶端位址或任何內容，避免個資與標籤爆量。
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, start_http_server

PUBLIC = "public"
UNMATCHED_ROUTE = "unmatched"

_SURFACES = (("/api/v1/public", PUBLIC), ("/api/v1/admin", "admin"), ("/api/v1/auth", "auth"))
_TASK_OUTCOMES = {"SUCCESS": "success", "FAILURE": "failure", "RETRY": "retry"}

TASK_RUNS = Counter("ncu_task_runs", "Celery 任務執行次數（依結果）", ["task", "outcome"])
TASK_DURATION = Histogram(
    "ncu_task_duration_seconds",
    "Celery 任務執行時間",
    ["task"],
    buckets=(0.1, 0.5, 1, 5, 15, 30, 60, 120, 300, 600, 1200),
)
AI_RUNS = Counter("ncu_ai_runs", "Skill 執行次數（依狀態）", ["skill", "status"])
AI_LATENCY = Histogram(
    "ncu_ai_run_latency_seconds",
    "Skill 執行的模型延遲（含重試）",
    ["skill"],
    buckets=(0.5, 1, 2, 5, 10, 20, 30, 60, 120, 300),
)
AI_COST = Counter("ncu_ai_cost_usd", "Skill 執行費用（USD）", ["skill", "model"])
AI_DAILY_LIMIT = Gauge(
    "ncu_ai_daily_cost_limit_usd", "config/llm.yaml 的每日費用上限（USD），供告警比較"
)
HTTP_REQUESTS = Counter(
    "ncu_http_requests",
    "HTTP 請求數（依介面、路由樣板、方法、狀態類別）",
    ["surface", "route", "method", "status_class"],
)
HTTP_DURATION = Histogram(
    "ncu_http_request_duration_seconds",
    "HTTP 請求處理時間",
    ["surface"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)
RATE_LIMITED = Counter("ncu_public_rate_limited", "公開端被限速（429）的次數", ["limiter"])

RATE_LIMITERS = ("public_read", "report")
for _limiter in RATE_LIMITERS:  # 先以 0 建立序列，increase() 才看得到第一次限速
    RATE_LIMITED.labels(limiter=_limiter)

_server_port: int | None = None


def observe_task(task: str, state: str, seconds: float | None) -> None:
    """記錄一次任務結束；`state` 為 Celery 狀態（SUCCESS／FAILURE／RETRY…）。"""
    TASK_RUNS.labels(task=task, outcome=_TASK_OUTCOMES.get(state, "other")).inc()
    if seconds is not None:
        TASK_DURATION.labels(task=task).observe(seconds)


def observe_ai_run(
    skill: str,
    model: str,
    status: str,
    *,
    latency_ms: int,
    cost_usd: float,
    daily_limit_usd: float,
) -> None:
    """記錄一次 Skill 執行的狀態、延遲與費用，並更新每日上限。"""
    AI_RUNS.labels(skill=skill, status=status).inc()
    AI_LATENCY.labels(skill=skill).observe(latency_ms / 1000)
    AI_COST.labels(skill=skill, model=model).inc(cost_usd)
    set_daily_cost_limit(daily_limit_usd)


def set_daily_cost_limit(usd: float) -> None:
    """更新每日費用上限指標（值來自 config/llm.yaml）。"""
    AI_DAILY_LIMIT.set(usd)


def surface_of(path: str) -> str:
    """請求路徑所屬的介面：public、admin、auth 或 other。"""
    for prefix, surface in _SURFACES:
        if path == prefix or path.startswith(prefix + "/"):
            return surface
    return "other"


def observe_http(path: str, route: str | None, method: str, status: int, seconds: float) -> None:
    """記錄一次 HTTP 請求；`route` 為路由樣板，沒有對應路由時記為 unmatched。"""
    surface = surface_of(path)
    HTTP_REQUESTS.labels(
        surface=surface,
        route=route or UNMATCHED_ROUTE,
        method=method,
        status_class=f"{status // 100}xx",
    ).inc()
    HTTP_DURATION.labels(surface=surface).observe(seconds)


def count_rate_limited(limiter: str) -> None:
    """公開端回 429 時呼叫：`public_read`（公開唯讀 API）或 `report`（匿名回報）。"""
    RATE_LIMITED.labels(limiter=limiter).inc()


def start_metrics_server(port: int) -> None:
    """在獨立埠輸出 `/metrics`；同一行程只啟動一次。"""
    global _server_port
    if _server_port is not None:
        return
    start_http_server(port, addr="0.0.0.0")  # noqa: S104 — 只在 Docker 內部網路，不發布到主機
    _server_port = port
