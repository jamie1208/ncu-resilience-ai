"""P0-T02：健康檢查、統一回應格式、request_id 與設定讀取。"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.main import create_app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


def test_health_returns_200_with_envelope(client: TestClient) -> None:
    res = client.get("/api/v1/health")
    assert res.status_code == 200
    body = res.json()
    assert set(body) == {"data", "meta", "error"}
    assert body["error"] is None
    assert body["data"]["status"] == "ok"
    assert body["data"]["service"] == get_settings().app_name


def test_health_meta_has_request_id_and_aware_utc_timestamp(client: TestClient) -> None:
    res = client.get("/api/v1/health")
    meta = res.json()["meta"]
    uuid.UUID(meta["request_id"])
    assert res.headers["X-Request-ID"] == meta["request_id"]
    ts = datetime.fromisoformat(meta["timestamp"])
    assert ts.utcoffset() is not None
    assert ts.utcoffset().total_seconds() == 0


def test_incoming_request_id_is_propagated(client: TestClient) -> None:
    rid = str(uuid.uuid4())
    res = client.get("/api/v1/health", headers={"X-Request-ID": rid})
    assert res.json()["meta"]["request_id"] == rid
    assert res.headers["X-Request-ID"] == rid


def test_invalid_incoming_request_id_is_replaced(client: TestClient) -> None:
    res = client.get("/api/v1/health", headers={"X-Request-ID": "bad\nvalue<script>"})
    rid = res.json()["meta"]["request_id"]
    uuid.UUID(rid)
    assert rid != "bad\nvalue<script>"


def test_settings_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NCU_ENVIRONMENT", "test")
    monkeypatch.setenv("NCU_LOG_LEVEL", "DEBUG")
    settings = Settings()
    assert settings.environment == "test"
    assert settings.log_level == "DEBUG"


def test_settings_reject_unknown_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NCU_ENVIRONMENT", "staging-typo")
    with pytest.raises(ValueError):
        Settings()
