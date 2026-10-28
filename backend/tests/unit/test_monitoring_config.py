"""P5-T04：infra/monitoring 的 Prometheus 設定、告警規則與 Grafana dashboard 跟程式指標一致。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from prometheus_client import REGISTRY

import app.core.metrics  # noqa: F401 — 註冊指標

REPO = Path(__file__).resolve().parents[3]
MONITORING = REPO / "infra" / "monitoring"
COMPOSE = REPO / "infra" / "docker" / "docker-compose.yml"
METRIC = re.compile(r"\bncu_[a-z_]+\b")


def load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def dashboard() -> dict[str, Any]:
    [path] = (MONITORING / "grafana" / "dashboards").glob("*.json")
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def exported_names() -> set[str]:
    names: set[str] = set()
    for collector in REGISTRY.collect():
        for sample in collector.samples:
            names.add(sample.name)
    for name in list(REGISTRY._names_to_collectors):  # 尚無樣本的帶標籤指標
        names.add(name)
    return names


def expressions() -> list[str]:
    exprs = [
        target["expr"] for panel in dashboard()["panels"] for target in panel.get("targets", [])
    ]
    for group in load_yaml(MONITORING / "prometheus" / "alerts.yml")["groups"]:
        exprs += [rule["expr"] for rule in group["rules"]]
    return exprs


def test_every_referenced_metric_is_exported() -> None:
    known = exported_names()
    referenced = {name for expr in expressions() for name in METRIC.findall(expr)}
    assert referenced, "dashboard 與告警規則應引用 ncu_* 指標"
    assert referenced <= known, sorted(referenced - known)


@pytest.mark.parametrize(
    "metric",
    [
        "ncu_task_runs_total",
        "ncu_ai_run_latency_seconds_bucket",
        "ncu_ai_cost_usd_total",
        "ncu_http_requests_total",
        "ncu_public_rate_limited_total",
    ],
)
def test_dashboard_shows_required_metric(metric: str) -> None:
    text = " ".join(
        target["expr"] for panel in dashboard()["panels"] for target in panel.get("targets", [])
    )
    assert metric in text


def test_dashboard_uses_provisioned_datasource() -> None:
    datasource = load_yaml(
        MONITORING / "grafana" / "provisioning" / "datasources" / "prometheus.yml"
    )
    uid = datasource["datasources"][0]["uid"]
    for panel in dashboard()["panels"]:
        if panel.get("type") == "row":
            continue
        assert panel["datasource"]["uid"] == uid, panel["title"]


def test_alert_rules_cover_required_signals() -> None:
    groups = load_yaml(MONITORING / "prometheus" / "alerts.yml")["groups"]
    rules = {rule["alert"]: rule for group in groups for rule in group["rules"]}
    joined = " ".join(rule["expr"] for rule in rules.values())
    for metric in (
        "ncu_task_runs_total",
        "ncu_ai_run_latency_seconds_bucket",
        "ncu_ai_cost_usd_total",
        "ncu_http_requests_total",
        "ncu_public_rate_limited_total",
    ):
        assert metric in joined
    for rule in rules.values():
        assert rule["labels"]["severity"] in {"warning", "critical"}
        assert rule["annotations"]["summary"]


def test_cost_alert_reads_limit_from_config_metric() -> None:
    """費用上限只能在 config/llm.yaml；告警規則以指標比較，不寫死金額。"""
    groups = load_yaml(MONITORING / "prometheus" / "alerts.yml")["groups"]
    cost_rules = [
        rule["expr"]
        for group in groups
        for rule in group["rules"]
        if "ncu_ai_cost_usd_total" in rule["expr"]
    ]
    assert cost_rules
    for expr in cost_rules:
        assert "ncu_ai_daily_cost_limit_usd" in expr


def test_prometheus_scrapes_api_and_worker_and_loads_rules() -> None:
    config = load_yaml(MONITORING / "prometheus" / "prometheus.yml")
    targets = {
        target
        for job in config["scrape_configs"]
        for static in job.get("static_configs", [])
        for target in static["targets"]
    }
    assert {"api:9100", "worker:9100"} <= targets
    assert "alerts.yml" in " ".join(config["rule_files"])


def test_compose_keeps_monitoring_local() -> None:
    services = load_yaml(COMPOSE)["services"]
    for name in ("prometheus", "grafana"):
        ports = services[name]["ports"]
        assert ports and all(str(p).startswith("127.0.0.1:") for p in ports), name
    for name in ("api", "worker"):
        ports = [str(p) for p in services[name].get("ports", [])]
        assert not any("9100" in p for p in ports), f"{name} 的指標埠不得發布到主機"
    grafana_env = services["grafana"]["environment"]
    assert grafana_env["GF_AUTH_ANONYMOUS_ENABLED"] == "false"
    assert "${GRAFANA_ADMIN_PASSWORD:?" in grafana_env["GF_SECURITY_ADMIN_PASSWORD"]


@pytest.mark.parametrize("legend", ["5xx", "24 小時費用"])
def test_sparse_series_show_zero_instead_of_no_data(legend: str) -> None:
    [expr] = [
        target["expr"]
        for panel in dashboard()["panels"]
        for target in panel.get("targets", [])
        if target["legendFormat"] == legend
    ]
    assert "or vector(0)" in expr
